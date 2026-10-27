#!/usr/bin/env python3
"""Processa o CSV bruto da bateria de simulacoes: valida, agrega
estatisticas (media/desvio por protocolo x area x velocidade) e gera
os 4 graficos obrigatorios (PDR, throughput, delay, jitter x velocidade),
separando o cenario-base (area=500m) da extensao do grupo (area=250m).

Uso:
    python3 scripts/analyze.py
    python3 scripts/analyze.py --input /outro/caminho/resultados.csv
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RAW_CSV = REPO_DIR / "data" / "raw" / "resultados-aodv-olsr-dsdv.csv"
PROCESSED_DIR = REPO_DIR / "data" / "processed"
FIGURES_DIR = REPO_DIR / "figures"

# Uma metrica -> (rotulo do eixo Y, nome base do arquivo de figura)
METRICAS = {
    "pdr_percent": ("PDR (%)", "pdr_vs_velocidade"),
    "throughput_mbps": ("Throughput (Mbps)", "throughput_vs_velocidade"),
    "atraso_medio_ms": ("Atraso medio (ms)", "delay_vs_velocidade"),
    "jitter_medio_ms": ("Jitter medio (ms)", "jitter_vs_velocidade"),
}

PROTOCOLOS = ["AODV", "OLSR", "DSDV"]
REPETICOES_ESPERADAS = 5

# Paleta Okabe-Ito (seguro para daltonismo), uma cor fixa por protocolo
CORES = {"AODV": "#0072B2", "OLSR": "#E69F00", "DSDV": "#009E73"}
MARCADORES = {"AODV": "o", "OLSR": "s", "DSDV": "^"}


def carregar_dados(caminho):
    if not caminho.exists():
        sys.exit(
            f"Nao encontrei {caminho}.\n"
            "Rode a bateria completa (./scripts/run_batch.sh) e confira se o CSV "
            "foi copiado para data/raw/ antes de rodar esta analise."
        )
    with open(caminho, newline="") as f:
        return list(csv.DictReader(f))


def validar(linhas, repeticoes_esperadas=REPETICOES_ESPERADAS):
    problemas = []
    contagem = defaultdict(int)
    for r in linhas:
        chave = (r["protocolo"], r["area_m"], r["velocidade_m_s"])
        contagem[chave] += 1
        pdr = float(r["pdr_percent"])
        if pdr < 0 or pdr > 100:
            problemas.append(f"PDR fora de 0-100 em {chave}, execucao {r['execucao']}: {pdr}")
        if int(r["pacotes_enviados"]) == 0:
            problemas.append(f"Nenhum pacote enviado em {chave}, execucao {r['execucao']}")

    for chave, n in contagem.items():
        if repeticoes_esperadas is not None and n != repeticoes_esperadas:
            problemas.append(f"{chave} tem {n} execucoes (esperado {repeticoes_esperadas})")

    if problemas:
        print(f"Avisos de validacao ({len(problemas)}):")
        for p in problemas:
            print(f"  - {p}")
    else:
        print("Validacao ok: nenhum problema encontrado.")
    return linhas


def agregar(linhas):
    grupos = defaultdict(list)
    for r in linhas:
        chave = (r["protocolo"], float(r["area_m"]), float(r["velocidade_m_s"]))
        grupos[chave].append(r)

    agregados = []
    for (protocolo, area, velocidade), grupo in sorted(grupos.items()):
        linha = {"protocolo": protocolo, "area_m": area, "velocidade_m_s": velocidade, "n": len(grupo)}
        for col in METRICAS:
            valores = [float(r[col]) for r in grupo]
            linha[f"{col}_media"] = mean(valores)
            linha[f"{col}_desvio"] = stdev(valores) if len(valores) > 1 else 0.0
        agregados.append(linha)
    return agregados


def salvar_processado(agregados):
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    destino = PROCESSED_DIR / "resultados-agregados.csv"
    campos = list(agregados[0].keys())
    with open(destino, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(agregados)
    print(f"Estatisticas agregadas salvas em {destino}")


def gerar_graficos(agregados, area_alvo, sufixo, rotulo_area):
    dados_area = [a for a in agregados if a["area_m"] == area_alvo]
    if not dados_area:
        print(f"Sem dados para area={area_alvo} m, pulando graficos '{sufixo}'.")
        return

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    for coluna, (rotulo_y, nome_base) in METRICAS.items():
        fig, ax = plt.subplots(figsize=(7, 5))
        for protocolo in PROTOCOLOS:
            pontos = sorted(
                (a["velocidade_m_s"], a[f"{coluna}_media"], a[f"{coluna}_desvio"])
                for a in dados_area
                if a["protocolo"] == protocolo
            )
            if not pontos:
                continue
            x = [p[0] for p in pontos]
            y = [p[1] for p in pontos]
            erro = [p[2] for p in pontos]
            # Desvio-padrao pode fazer a barra ultrapassar limites fisicos
            # (por exemplo, PDR abaixo de 0%). Mantemos a media e recortamos
            # apenas a barra de erro para a faixa fisicamente valida.
            limite_inferior = 0.0
            limite_superior = 100.0 if coluna == "pdr_percent" else None
            erro_inferior = [min(e, max(0.0, valor - limite_inferior)) for valor, e in zip(y, erro)]
            erro_superior = [
                min(e, max(0.0, limite_superior - valor))
                if limite_superior is not None else e
                for valor, e in zip(y, erro)
            ]
            ax.errorbar(
                x, y, yerr=[erro_inferior, erro_superior],
                marker=MARCADORES[protocolo], markersize=7, linewidth=2,
                capsize=4, label=protocolo, color=CORES[protocolo],
            )

        ax.set_xlabel("Velocidade (m/s)")
        ax.set_ylabel(rotulo_y)
        ax.set_title(f"{rotulo_y} x Velocidade — {rotulo_area}")
        ax.set_ylim(bottom=0)
        if coluna == "pdr_percent":
            ax.set_ylim(0, 100)
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        destino = FIGURES_DIR / f"{nome_base}_{sufixo}.png"
        fig.savefig(destino, dpi=150)
        plt.close(fig)
        print(f"Grafico salvo: {destino}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_RAW_CSV, help="Caminho do CSV bruto")
    parser.add_argument(
        "--expected-runs", type=int, default=REPETICOES_ESPERADAS,
        help="Numero esperado de repeticoes por combinacao (use 0 para nao validar)",
    )
    args = parser.parse_args()

    linhas = carregar_dados(args.input)
    repeticoes_esperadas = args.expected_runs if args.expected_runs > 0 else None
    linhas = validar(linhas, repeticoes_esperadas)
    agregados = agregar(linhas)
    salvar_processado(agregados)
    gerar_graficos(agregados, area_alvo=500.0, sufixo="area500_base", rotulo_area="cenario-base, 500x500 m")
    gerar_graficos(agregados, area_alvo=250.0, sufixo="area250_extensao", rotulo_area="extensao do grupo, 250x250 m")


if __name__ == "__main__":
    main()
