"""Interface local para executar e analisar as simulacoes MANET.

Executar no Windows/PowerShell ou no Ubuntu:
    streamlit run scripts/dashboard.py

O dashboard e opcional e nao substitui scripts/analyze.py. Ele usa o
simulador existente no WSL no Windows, ou diretamente no Ubuntu, e os mesmos
arquivos CSV/PNG do repositorio.
"""

from __future__ import annotations

import io
import os
import shlex
import signal
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

# Reaproveita a agregacao (IC 95%) e os graficos de scripts/analyze.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import COLUNAS_OBRIGATORIAS, agregar, criar_figura  # noqa: E402


REPO_DIR = Path(__file__).resolve().parent.parent
RAW_CSV = REPO_DIR / "data" / "raw" / "resultados-aodv-olsr-dsdv.csv"
FIGURES_DIR = REPO_DIR / "figures"
LOG_DIR = REPO_DIR / "logs"

# Metrica -> nome curto usado no arquivo PNG exportado
METRICS = {
    "pdr_percent": "pdr",
    "throughput_kbps": "throughput",
    "atraso_medio_ms": "delay",
    "jitter_medio_ms": "jitter",
}


def uses_wsl() -> bool:
    """Retorna se o dashboard deve executar o simulador dentro do WSL."""
    return os.name == "nt"


def to_wsl_path(path: Path, distro: str) -> str:
    """Converte um caminho Windows para o caminho visto pelo WSL.

    Em sistemas Unix o caminho já é válido para o processo local. Manter essa
    função multiplataforma evita que o restante do dashboard precise conhecer
    os detalhes do ambiente de execução.
    """
    if not uses_wsl():
        return str(path)
    try:
        result = subprocess.run(
            ["wsl.exe", "-d", distro, "--", "wslpath", "-a", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        drive = path.drive.rstrip(":").lower()
        if not drive:
            return str(path)
        return f"/mnt/{drive}/{path.as_posix().split(':/', 1)[-1]}"


def start_simulation(
    distro: str, ns3_dir: str, n_nodes: int, sim_time: float, n_runs: int, alcance: float,
    workers: int,
) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "dashboard-run.log"
    repo_runtime = to_wsl_path(REPO_DIR, distro)
    command = f"""set -e
NS3_DIR={shlex.quote(ns3_dir)}
NS3_DIR="${{NS3_DIR/#\\~/$HOME}}"
REPO_DIR={shlex.quote(repo_runtime)}
exec python3 "$REPO_DIR/scripts/run_batch_parallel.py" --ns3-dir "$NS3_DIR" \\
    --workers {workers} --nNodes {n_nodes} --simTime {sim_time:g} --nRuns {n_runs} \\
    --alcance {alcance:g}
"""
    if uses_wsl():
        runner = ["wsl.exe", "-d", distro, "--", "bash", "-lc", command]
    else:
        runner = [shutil.which("bash") or "bash", "-lc", command]

    log_file = log_path.open("w", encoding="utf-8")
    try:
        popen_options = {
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        }
        if os.name != "nt":
            popen_options["start_new_session"] = True
        process = subprocess.Popen(runner, **popen_options)
    finally:
        log_file.close()
    st.session_state.process = process
    st.session_state.log_path = log_path
    st.session_state.run_config = {
        "nNodes": n_nodes,
        "simTime": sim_time,
        "nRuns": n_runs,
        "alcance": alcance,
        "workers": workers,
        "total": 3 * 2 * 4 * n_runs,
    }
    return log_path


def stop_simulation(process: subprocess.Popen, timeout: float = 10.0) -> None:
    """Encerra o processo do dashboard e os processos filhos do ns-3."""
    if process.poll() is not None:
        return

    if os.name == "nt":
        # CTRL_BREAK encerra o grupo criado com CREATE_NEW_PROCESS_GROUP.
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            process.terminate()
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=timeout)


def simulation_running(distro: str) -> bool:
    """Evita iniciar duas baterias que escreveriam no mesmo CSV."""
    if uses_wsl():
        command = [
            "wsl.exe",
            "-d",
            distro,
            "--",
            "bash",
            "-lc",
            "pgrep -f '[/]build/scratch/.*manet-routing'",
        ]
    else:
        command = [shutil.which("pgrep") or "pgrep", "-f", "[/]build/scratch/.*manet-routing"]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        # Se a ferramenta de inspeção de processos não estiver instalada,
        # não bloqueie o início da simulação. O processo iniciado pelo
        # próprio dashboard ainda é acompanhado por session_state.process.
        return False
    return result.returncode == 0


def read_results() -> pd.DataFrame | None:
    if not RAW_CSV.exists() or RAW_CSV.stat().st_size == 0:
        return None
    try:
        df = pd.read_csv(RAW_CSV)
    except (OSError, pd.errors.ParserError) as exc:
        st.error(f"Nao foi possivel ler o CSV: {exc}")
        return None
    if "throughput_mbps" in df.columns:
        st.error("O CSV em data/raw/ foi gerado por uma versao antiga do simulador (PDR do OLSR, "
                 "throughput e atraso/jitter calculados de forma incorreta). Rode a bateria novamente.")
        return None
    missing = [column for column in COLUNAS_OBRIGATORIAS if column not in df.columns]
    if missing:
        st.error(f"Colunas ausentes no CSV: {', '.join(missing)}")
        return None
    return df


def make_figure(aggregated: list[dict], area: float, metric: str):
    fig = criar_figura(aggregated, area, metric, f"area {area:g}x{area:g} m")
    return fig, f"{METRICS[metric]}_area{int(area)}.png"


def render_results(df: pd.DataFrame):
    aggregated = agregar(df.to_dict("records"))
    grouped = pd.DataFrame(aggregated)
    st.subheader("Resumo dos resultados")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Execucoes", len(df))
    c2.metric("Protocolos", df["protocolo"].nunique())
    c3.metric("Areas", df["area_m"].nunique())
    c4.metric("Velocidades", df["velocidade_m_s"].nunique())

    st.dataframe(grouped, use_container_width=True, hide_index=True)
    st.subheader("Graficos")
    for area in sorted(grouped["area_m"].unique()):
        st.markdown(f"#### Area {area:g} x {area:g} m")
        columns = st.columns(2)
        for index, metric in enumerate(METRICS):
            figure, filename = make_figure(aggregated, area, metric)
            with columns[index % 2]:
                st.pyplot(figure, clear_figure=False)
                figure_bytes = io.BytesIO()
                figure.savefig(figure_bytes, format="png", dpi=150)
                st.download_button(
                    f"Baixar {filename}",
                    data=figure_bytes.getvalue(),
                    file_name=filename,
                    mime="image/png",
                    key=f"download-{filename}",
                )
                FIGURES_DIR.mkdir(parents=True, exist_ok=True)
                figure.savefig(FIGURES_DIR / filename, dpi=150)
            plt.close(figure)

    processed_csv = grouped.to_csv(index=False).encode("utf-8")
    raw_csv = df.to_csv(index=False).encode("utf-8")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("resultados-brutos.csv", raw_csv)
        bundle.writestr("resultados-agregados.csv", processed_csv)
    st.download_button("Exportar dados agregados (CSV)", processed_csv, "resultados-agregados.csv", "text/csv")
    st.download_button("Exportar dados brutos (CSV)", raw_csv, "resultados-brutos.csv", "text/csv")
    st.download_button("Exportar CSVs em ZIP", archive.getvalue(), "resultados-manet.zip", "application/zip")


def render_process_status():
    """Mostra o estado atual do processo iniciado pelo dashboard."""
    process = st.session_state.process
    if process is None:
        return
    if process.poll() is None:
        st.warning("Simulacao em andamento. O status e atualizado automaticamente.")
    elif st.session_state.interrupted:
        st.info("A simulacao foi interrompida. O CSV anterior foi preservado.")
    elif process.returncode == 0:
        st.success("Simulacao concluida e CSV copiado para data/raw/.")
    else:
        st.error(f"A simulacao terminou com codigo {process.returncode}.")


def render_auto_status():
    """Atualiza somente o status a cada dois segundos quando suportado."""
    process = st.session_state.process
    if process is None or process.poll() is not None:
        render_process_status()
        return
    fragment = getattr(st, "fragment", None)
    if fragment is None:
        render_process_status()
        return
    auto_status = fragment(render_process_status, run_every="2s")
    auto_status()


def main():
    st.set_page_config(page_title="MANET + ns-3", page_icon="📡", layout="wide")
    st.title("📡 MANET + ns-3")
    st.caption("Execucao, acompanhamento e analise local de AODV, OLSR e DSDV")

    st.session_state.setdefault("process", None)
    st.session_state.setdefault("log_path", LOG_DIR / "dashboard-run.log")
    st.session_state.setdefault("interrupted", False)

    with st.sidebar:
        st.header("Configuracao da execucao")
        if uses_wsl():
            distro = st.text_input("Distribuicao WSL", "Ubuntu-24.04")
            ns3_label = "Pasta do ns-3 no WSL"
            ns3_default = "/home/joaoa/ns-3.48"
        else:
            distro = ""
            st.info("Ubuntu detectado: a simulacao sera executada localmente.")
            ns3_label = "Pasta local do ns-3"
            ns3_default = str(Path.home() / "ns-3.48")
        ns3_dir = st.text_input(ns3_label, ns3_default)
        n_nodes = st.number_input("Numero de nos", min_value=2, value=20, step=1)
        sim_time = st.number_input("Tempo de simulacao (s)", min_value=1.0, value=120.0, step=10.0)
        n_runs = st.number_input("Repeticoes por combinacao", min_value=1, value=5, step=1)
        alcance = st.number_input("Alcance do radio (m)", min_value=1.0, value=250.0, step=10.0)
        workers = st.number_input("Processos simultaneos", min_value=1, value=4, step=1)
        st.info("O simulador atual usa 3 protocolos, 2 areas e 4 velocidades fixas. Total estimado: "
                f"{3 * 2 * 4 * n_runs} execucoes.")
        start = st.button("▶ Iniciar simulacao", use_container_width=True, type="primary")
        current_process = st.session_state.process
        running = current_process is not None and current_process.poll() is None
        stop = st.button("■ Interromper simulacao", use_container_width=True, disabled=not running)
        refresh = st.button("Atualizar status", use_container_width=True)

    if start:
        process = st.session_state.process
        if process is not None and process.poll() is None:
            st.warning("Ja existe uma simulacao em execucao.")
        elif simulation_running(distro):
            st.warning("O ns-3 ja possui uma simulacao manet-routing em execucao.")
        else:
            try:
                log_path = start_simulation(
                    distro, ns3_dir, int(n_nodes), float(sim_time), int(n_runs), float(alcance),
                    int(workers),
                )
                st.session_state.interrupted = False
                st.success(f"Simulacao iniciada. Log: {log_path}")
            except OSError as exc:
                st.error(f"Nao foi possivel iniciar a simulacao: {exc}")

    if stop:
        process = st.session_state.process
        if process is not None and process.poll() is None:
            try:
                stop_simulation(process)
                st.session_state.interrupted = True
                st.info("Simulacao interrompida.")
            except (OSError, subprocess.TimeoutExpired) as exc:
                st.error(f"Nao foi possivel interromper a simulacao: {exc}")

    render_auto_status()

    log_path = Path(st.session_state.log_path)
    if log_path.exists():
        with st.expander("Log da execucao", expanded=True):
            st.code("\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]))

    df = read_results()
    if df is None:
        st.info("Ainda nao ha resultados. Configure a execucao e clique em 'Iniciar simulacao'.")
    else:
        render_results(df)


if __name__ == "__main__":
    main()
