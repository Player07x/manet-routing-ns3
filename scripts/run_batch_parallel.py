#!/usr/bin/env python3
"""Executa a bateria do ns-3 em processos independentes em paralelo.

O ns-3 usa estado global no processo (Simulator, configuracao e RNG), por isso
o paralelismo e feito por processos, e nao por ``std::thread`` dentro de uma
unica simulacao. Cada tarefa grava um CSV proprio; somente depois que todas
terminam os arquivos sao combinados no CSV final.

Uso:
    python3 scripts/run_batch_parallel.py --ns3-dir ~/ns-3.48 --workers 4
"""

from __future__ import annotations

import argparse
import csv
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parent.parent
SOURCE = REPO_DIR / "src" / "manet-routing.cc"
DEFAULT_OUTPUT = REPO_DIR / "data" / "raw" / "resultados-aodv-olsr-dsdv.csv"
FIELDNAMES = [
    "protocolo",
    "area_m",
    "velocidade_m_s",
    "execucao",
    "pacotes_enviados",
    "pacotes_recebidos",
    "pdr_percent",
    "atraso_medio_ms",
    "throughput_kbps",
    "jitter_medio_ms",
]


@dataclass(frozen=True)
class Job:
    index: int
    protocolo: str
    area: float
    velocidade: float
    run: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns3-dir", type=Path, default=Path.home() / "ns-3.48")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1),
                        help="Numero de processos de simulacao simultaneos")
    parser.add_argument("--nNodes", type=int, default=20)
    parser.add_argument("--simTime", type=float, default=120.0)
    parser.add_argument("--nRuns", type=int, default=5)
    parser.add_argument("--alcance", type=float, default=250.0,
                        help="Alcance do radio em metros (modelo de disco)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def run_command(command: list[str], cwd: Path, log_path: Path) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    if completed.returncode != 0:
        raise RuntimeError(
            f"comando falhou com codigo {completed.returncode}; consulte {log_path}"
        )


def run_job(
    job: Job, ns3_dir: Path, n_nodes: int, sim_time: float, alcance: float, work_dir: Path
) -> Path:
    output = work_dir / f"job-{job.index:03d}.csv"
    log_path = work_dir / f"job-{job.index:03d}.log"
    command = [
        str(ns3_dir / "ns3"),
        "run",
        (
            f"scratch/manet-routing --nNodes={n_nodes} --simTime={sim_time:g} "
            f"--alcance={alcance:g} "
            f"--nRuns=1 --protocolo={job.protocolo} --area={job.area:g} "
            f"--velocidade={job.velocidade:g} --run={job.run} "
            f"--output={shlex.quote(str(output))}"
        ),
    ]
    run_command(command, ns3_dir, log_path)
    if not output.exists():
        raise RuntimeError(f"a tarefa {job.index} nao produziu {output}; consulte {log_path}")
    return output


def read_job_csv(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))
    if len(rows) != 1:
        raise RuntimeError(f"{path} deveria conter uma linha, mas contem {len(rows)}")
    if set(rows[0]) != set(FIELDNAMES):
        raise RuntimeError(f"cabecalho inesperado em {path}: {list(rows[0])}")
    return rows[0]


def merge_results(paths: list[Path], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
            writer.writeheader()
            for path in paths:
                writer.writerow(read_job_csv(path))
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    args = parse_args()
    ns3_dir = args.ns3_dir.expanduser().resolve()
    output = args.output.expanduser().resolve()
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

    jobs = [
        Job(index, protocolo, area, velocidade, run)
        for index, (protocolo, area, velocidade, run) in enumerate(
            (
                (protocolo, area, velocidade, run)
                for protocolo in ("AODV", "OLSR", "DSDV")
                for area in (500.0, 250.0)
                for velocidade in (1.0, 5.0, 10.0, 20.0)
                for run in range(1, args.nRuns + 1)
            ),
            start=1,
        )
    ]
    work_dir = Path(tempfile.mkdtemp(prefix=".manet-parallel-", dir=REPO_DIR / "logs"))
    print(f"Executando {len(jobs)} tarefas com {args.workers} processos...", flush=True)
    completed: dict[int, Path] = {}
    failures: list[str] = []
    success = False
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(
                    run_job, job, ns3_dir, args.nNodes, args.simTime, args.alcance, work_dir
                ): job
                for job in jobs
            }
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
        merge_results([completed[job.index] for job in jobs], output)
        success = True
    finally:
        if success and work_dir.exists():
            shutil.rmtree(work_dir)

    print(f"Resultado salvo em {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
