"""Interface local para executar e analisar as simulacoes MANET.

Executar no Windows/PowerShell:
    streamlit run scripts/dashboard.py

O dashboard e opcional e nao substitui scripts/analyze.py. Ele usa o
simulador existente no WSL e os mesmos arquivos CSV/PNG do repositorio.
"""

from __future__ import annotations

import io
import shlex
import subprocess
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st


REPO_DIR = Path(__file__).resolve().parent.parent
RAW_CSV = REPO_DIR / "data" / "raw" / "resultados-aodv-olsr-dsdv.csv"
FIGURES_DIR = REPO_DIR / "figures"
LOG_DIR = REPO_DIR / "logs"

METRICS = {
    "pdr_percent": ("PDR (%)", "pdr"),
    "throughput_mbps": ("Throughput (Mbps)", "throughput"),
    "atraso_medio_ms": ("Atraso medio (ms)", "delay"),
    "jitter_medio_ms": ("Jitter medio (ms)", "jitter"),
}
COLORS = {"AODV": "#0072B2", "OLSR": "#E69F00", "DSDV": "#009E73"}
MARKERS = {"AODV": "o", "OLSR": "s", "DSDV": "^"}


def to_wsl_path(path: Path, distro: str) -> str:
    """Converte um caminho Windows para o caminho visto pelo WSL."""
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
        return f"/mnt/{drive}/{path.as_posix().split(':/', 1)[-1]}"


def start_simulation(distro: str, ns3_dir: str, n_nodes: int, sim_time: float, n_runs: int) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "dashboard-run.log"
    repo_wsl = to_wsl_path(REPO_DIR, distro)
    command = f"""set -e
NS3_DIR={shlex.quote(ns3_dir)}
NS3_DIR="${{NS3_DIR/#\\~/$HOME}}"
REPO_DIR={shlex.quote(repo_wsl)}
cp "$REPO_DIR/src/manet-routing.cc" "$NS3_DIR/scratch/manet-routing.cc"
cd "$NS3_DIR"
./ns3 build manet-routing
./ns3 run "scratch/manet-routing --nNodes={n_nodes} --simTime={sim_time:g} --nRuns={n_runs}"
mkdir -p "$REPO_DIR/data/raw"
cp "$NS3_DIR/resultados-aodv-olsr-dsdv.csv" "$REPO_DIR/data/raw/"
"""
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        ["wsl.exe", "-d", distro, "--", "bash", "-lc", command],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    st.session_state.process = process
    st.session_state.log_path = log_path
    st.session_state.run_config = {
        "nNodes": n_nodes,
        "simTime": sim_time,
        "nRuns": n_runs,
        "total": 3 * 2 * 4 * n_runs,
    }
    return log_path


def simulation_running(distro: str) -> bool:
    """Evita iniciar duas baterias que escreveriam no mesmo CSV."""
    result = subprocess.run(
        ["wsl.exe", "-d", distro, "--", "bash", "-lc", "pgrep -f '[/]build/scratch/.*manet-routing'"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def read_results() -> pd.DataFrame | None:
    if not RAW_CSV.exists() or RAW_CSV.stat().st_size == 0:
        return None
    try:
        return pd.read_csv(RAW_CSV)
    except (OSError, pd.errors.ParserError) as exc:
        st.error(f"Nao foi possivel ler o CSV: {exc}")
        return None


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    numeric = list(METRICS)
    grouped = (
        df.groupby(["protocolo", "area_m", "velocidade_m_s"])[numeric]
        .agg(["mean", "std"])
        .reset_index()
    )
    grouped.columns = [
        "protocolo",
        "area_m",
        "velocidade_m_s",
        *[f"{metric}_{stat}" for metric in numeric for stat in ("mean", "std")],
    ]
    return grouped.fillna(0)


def make_figure(grouped: pd.DataFrame, area: float, metric: str):
    label, short_name = METRICS[metric]
    area_data = grouped[grouped["area_m"] == area]
    fig, ax = plt.subplots(figsize=(8, 5))
    for protocol in ("AODV", "OLSR", "DSDV"):
        points = area_data[area_data["protocolo"] == protocol].sort_values("velocidade_m_s")
        if points.empty:
            continue
        x = points["velocidade_m_s"].to_numpy()
        y = points[f"{metric}_mean"].to_numpy()
        std = points[f"{metric}_std"].to_numpy()
        lower = np_min(std, y)
        upper = std
        if metric == "pdr_percent":
            upper = np_min(std, 100 - y)
        ax.errorbar(
            x,
            y,
            yerr=[lower, upper],
            marker=MARKERS[protocol],
            linewidth=2,
            capsize=4,
            label=protocol,
            color=COLORS[protocol],
        )
    ax.set_xlabel("Velocidade (m/s)")
    ax.set_ylabel(label)
    ax.set_title(f"{label} x Velocidade — area {area:g}x{area:g} m")
    ax.set_ylim(0, 100) if metric == "pdr_percent" else ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    return fig, f"{short_name}_area{int(area)}.png"


def np_min(left, right):
    """Minimo elemento a elemento sem exigir numpy como dependencia direta."""
    return [min(float(a), max(0.0, float(b))) for a, b in zip(left, right)]


def render_results(df: pd.DataFrame):
    grouped = aggregate(df)
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
            figure, filename = make_figure(grouped, area, metric)
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


def main():
    st.set_page_config(page_title="MANET + ns-3", page_icon="📡", layout="wide")
    st.title("📡 MANET + ns-3")
    st.caption("Execucao, acompanhamento e analise local de AODV, OLSR e DSDV")

    st.session_state.setdefault("process", None)
    st.session_state.setdefault("log_path", LOG_DIR / "dashboard-run.log")

    with st.sidebar:
        st.header("Configuracao da execucao")
        distro = st.text_input("Distribuicao WSL", "Ubuntu-24.04")
        ns3_dir = st.text_input("Pasta do ns-3 no WSL", "/home/joaoa/ns-3.48")
        n_nodes = st.number_input("Numero de nos", min_value=2, value=20, step=1)
        sim_time = st.number_input("Tempo de simulacao (s)", min_value=1.0, value=120.0, step=10.0)
        n_runs = st.number_input("Repeticoes por combinacao", min_value=1, value=5, step=1)
        st.info("O simulador atual usa 3 protocolos, 2 areas e 4 velocidades fixas. Total estimado: "
                f"{3 * 2 * 4 * n_runs} execucoes.")
        start = st.button("▶ Iniciar simulacao", use_container_width=True, type="primary")
        refresh = st.button("Atualizar status", use_container_width=True)

    if start:
        process = st.session_state.process
        if process is not None and process.poll() is None:
            st.warning("Ja existe uma simulacao em execucao.")
        elif simulation_running(distro):
            st.warning("O ns-3 ja possui uma simulacao manet-routing em execucao.")
        else:
            try:
                log_path = start_simulation(distro, ns3_dir, int(n_nodes), float(sim_time), int(n_runs))
                st.success(f"Simulacao iniciada. Log: {log_path}")
            except OSError as exc:
                st.error(f"Nao foi possivel iniciar o WSL: {exc}")

    process = st.session_state.process
    if process is not None:
        if process.poll() is None:
            st.warning("Simulacao em andamento. Use 'Atualizar status' para obter o progresso.")
        elif process.returncode == 0:
            st.success("Simulacao concluida e CSV copiado para data/raw/.")
        else:
            st.error(f"A simulacao terminou com codigo {process.returncode}.")

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
