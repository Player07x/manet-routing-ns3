#!/usr/bin/env python3
"""Executa a bateria do ns-3 em processos independentes em paralelo.

O ns-3 usa estado global no processo (Simulator, configuracao e RNG), por isso
o paralelismo e feito por processos, e nao por ``std::thread`` dentro de uma
unica simulacao. Cada tarefa grava CSVs proprios; somente depois que todas
terminam os arquivos sao combinados nos CSVs finais:

- data/raw/resultados-aodv-olsr-dsdv.csv  (uma linha por execucao)
- data/raw/resultados-por-fluxo.csv       (uma linha por fluxo de cada execucao)
- data/raw/execucoes.csv                  (log: parametros, versao e horario de cada execucao)
- data/raw/metadados.json                 (ambiente, commit e parametros da bateria)

Uso:
    python3 scripts/run_batch_parallel.py --ns3-dir ~/ns-3.48 --workers 4
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent.parent
SOURCE = REPO_DIR / "src" / "manet-routing.cc"
RAW_DIR = REPO_DIR / "data" / "raw"
DEFAULT_OUTPUT = RAW_DIR / "resultados-aodv-olsr-dsdv.csv"
DEFAULT_OUTPUT_FLOWS = RAW_DIR / "resultados-por-fluxo.csv"
DEFAULT_LOG = RAW_DIR / "execucoes.csv"
DEFAULT_METADATA = RAW_DIR / "metadados.json"
PROTOCOLS = ("AODV", "OLSR", "DSDV")
AREAS = (500.0, 750.0)  # cenario-base e extensao do grupo
SPEEDS = (1.0, 5.0, 10.0, 20.0)
FIELDNAMES = [
    "protocolo", "area_m", "velocidade_m_s", "seed", "execucao", "pacotes_enviados",
    "pacotes_recebidos", "pdr_percent", "atraso_medio_ms", "throughput_mbps", "jitter_medio_ms",
    "atraso_p50_ms", "atraso_p95_ms", "pacotes_tx_ip", "descartes_mac",
]
FLOW_FIELDNAMES = [
    "protocolo", "area_m", "velocidade_m_s", "seed", "execucao", "fluxo", "origem", "destino",
    "pacotes_enviados", "pacotes_tx_ip", "pacotes_recebidos", "pacotes_perdidos", "bytes_recebidos",
    "pdr_percent", "atraso_medio_ms", "throughput_mbps", "jitter_medio_ms", "atraso_p50_ms",
    "atraso_p95_ms", "descartes_mac",
]
LOG_FIELDNAMES = [
    "protocolo", "area_m", "velocidade_m_s", "seed", "execucao", "sim_time_s", "alcance_m", "n_nos",
    "arp", "versao_ns3", "commit_repositorio", "inicio_utc", "fim_utc", "duracao_s",
]


@dataclass(frozen=True)
class Job:
    index: int
    protocolo: str
    area: float
    velocidade: float
    run: int


@dataclass(frozen=True)
class JobResult:
    output: Path
    output_flows: Path
    start: str
    end: str
    seconds: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ns3-dir", type=Path, default=Path.home() / "ns-3.48")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1),
                        help="Numero de processos de simulacao simultaneos")
    parser.add_argument("--nNodes", type=int, default=20)
    parser.add_argument("--simTime", type=float, default=120.0)
    parser.add_argument("--nRuns", type=int, default=5)
    parser.add_argument("--alcance", type=float, default=250.0,
                        help="Alcance do radio em metros (modelo de disco)")
    parser.add_argument("--seed", type=int, default=1, help="Seed mestre (as repeticoes variam o run)")
    parser.add_argument("--arp", choices=("permanente", "dinamico"), default="permanente",
                        help="Tabelas ARP pre-preenchidas (padrao) ou ARP padrao do ns-3")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output-fluxos", type=Path, default=DEFAULT_OUTPUT_FLOWS)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--metadados", type=Path, default=DEFAULT_METADATA)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def command_output(command: list[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "desconhecido"


def repository_commit() -> str:
    commit = command_output(["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"])
    dirty = command_output(["git", "-C", str(REPO_DIR), "status", "--porcelain", "--", "src", "scripts"])
    return f"{commit}+alteracoes-locais" if dirty and dirty != "desconhecido" else commit


def run_command(command: list[str], cwd: Path, log_path: Path) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    if completed.returncode != 0:
        raise RuntimeError(
            f"comando falhou com codigo {completed.returncode}; consulte {log_path}"
        )


def run_job(job: Job, ns3_dir: Path, args: argparse.Namespace, work_dir: Path) -> JobResult:
    output = work_dir / f"job-{job.index:03d}.csv"
    output_flows = work_dir / f"job-{job.index:03d}-fluxos.csv"
    log_path = work_dir / f"job-{job.index:03d}.log"
    command = [
        str(ns3_dir / "ns3"),
        "run",
        "--no-build",
        (
            f"scratch/manet-routing --nNodes={args.nNodes} --simTime={args.simTime:g} "
            f"--alcance={args.alcance:g} --seed={args.seed} --arp={args.arp} "
            f"--nRuns=1 --protocolo={job.protocolo} --area={job.area:g} "
            f"--velocidade={job.velocidade:g} --run={job.run} "
            f"--output={shlex.quote(str(output))} --outputFluxos={shlex.quote(str(output_flows))}"
        ),
    ]
    start, t0 = utc_now(), time.monotonic()
    run_command(command, ns3_dir, log_path)
    end, seconds = utc_now(), time.monotonic() - t0
    for path in (output, output_flows):
        if not path.exists():
            raise RuntimeError(f"a tarefa {job.index} nao produziu {path}; consulte {log_path}")
    return JobResult(output, output_flows, start, end, seconds)


def read_job_csv(path: Path, fieldnames: list[str], expected_rows: int) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    if len(rows) != expected_rows:
        raise RuntimeError(f"{path} deveria conter {expected_rows} linha(s), mas contem {len(rows)}")
    if any(set(row) != set(fieldnames) for row in rows):
        raise RuntimeError(f"cabecalho inesperado em {path}: {list(rows[0])}")
    return rows


def write_atomically(destination: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    args = parse_args()
    ns3_dir = args.ns3_dir.expanduser().resolve()
    if args.workers < 1:
        raise SystemExit("--workers deve ser pelo menos 1")
    if args.nRuns < 1:
        raise SystemExit("--nRuns deve ser pelo menos 1")
    if not SOURCE.is_file():
        raise SystemExit(f"fonte nao encontrada: {SOURCE}")
    if not (ns3_dir / "ns3").is_file():
        raise SystemExit(f"nao encontrei o executavel ns3 em {ns3_dir}")

    scratch_source = ns3_dir / "scratch" / "manet-routing.cc"
    scratch_source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, scratch_source)
    build_log = REPO_DIR / "logs" / "parallel-build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    print(f"Compilando em {ns3_dir}...", flush=True)
    run_command([str(ns3_dir / "ns3"), "build", "manet-routing"], ns3_dir, build_log)

    ns3_version = (ns3_dir / "VERSION").read_text().strip() if (ns3_dir / "VERSION").exists() else "desconhecida"
    commit = repository_commit()
    started = utc_now()
    jobs = [
        Job(index, protocolo, area, velocidade, run)
        for index, (protocolo, area, velocidade, run) in enumerate(
            (
                (protocolo, area, velocidade, run)
                for protocolo in PROTOCOLS
                for area in AREAS
                for velocidade in SPEEDS
                for run in range(1, args.nRuns + 1)
            ),
            start=1,
        )
    ]
    work_dir = Path(tempfile.mkdtemp(prefix=".manet-parallel-", dir=REPO_DIR / "logs"))
    print(f"Executando {len(jobs)} tarefas com {args.workers} processos...", flush=True)
    completed: dict[int, JobResult] = {}
    failures: list[str] = []
    success = False
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(run_job, job, ns3_dir, args, work_dir): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    completed[job.index] = future.result()
                    print(f"[{len(completed)}/{len(jobs)}] {job.protocolo} area={job.area:g} "
                          f"vel={job.velocidade:g} run={job.run}", flush=True)
                except Exception as exc:
                    failures.append(f"tarefa {job.index} ({job.protocolo}, area={job.area:g}, "
                                    f"vel={job.velocidade:g}, run={job.run}): {exc}")
        if failures:
            print("Falha na bateria:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            print(f"Logs preservados em {work_dir}", file=sys.stderr)
            return 1

        rows, flow_rows, log_rows = [], [], []
        for job in jobs:
            result = completed[job.index]
            rows += read_job_csv(result.output, FIELDNAMES, 1)
            flow_rows += read_job_csv(result.output_flows, FLOW_FIELDNAMES, 4)
            log_rows.append({
                "protocolo": job.protocolo, "area_m": f"{job.area:g}", "velocidade_m_s": f"{job.velocidade:g}",
                "seed": args.seed, "execucao": job.run, "sim_time_s": f"{args.simTime:g}",
                "alcance_m": f"{args.alcance:g}", "n_nos": args.nNodes, "arp": args.arp, "versao_ns3": ns3_version,
                "commit_repositorio": commit, "inicio_utc": result.start, "fim_utc": result.end,
                "duracao_s": f"{result.seconds:.1f}",
            })
        write_atomically(args.output.expanduser().resolve(), FIELDNAMES, rows)
        write_atomically(args.output_fluxos.expanduser().resolve(), FLOW_FIELDNAMES, flow_rows)
        write_atomically(args.log.expanduser().resolve(), LOG_FIELDNAMES, log_rows)
        metadata = {
            "inicio_utc": started,
            "fim_utc": utc_now(),
            "commit_repositorio": commit,
            "versao_ns3": ns3_version,
            "compilador": command_output(["g++", "--version"]).splitlines()[0],
            "sistema": platform.freedesktop_os_release().get("PRETTY_NAME", platform.platform())
            if hasattr(platform, "freedesktop_os_release") else platform.platform(),
            "kernel": platform.release(),
            "python": platform.python_version(),
            "parametros": {
                "nNodes": args.nNodes, "simTime_s": args.simTime, "nRuns": args.nRuns,
                "alcance_m": args.alcance, "seed": args.seed, "arp": args.arp, "protocolos": list(PROTOCOLS),
                "areas_m": list(AREAS), "velocidades_m_s": list(SPEEDS),
            },
            "execucoes": len(jobs),
        }
        args.metadados.expanduser().resolve().write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
        success = True
    finally:
        if success and work_dir.exists():
            shutil.rmtree(work_dir)

    print(f"Resultados salvos em {args.output} e {args.output_fluxos}")
    print(f"Log das execucoes em {args.log}; metadados em {args.metadados}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
