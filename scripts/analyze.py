#!/usr/bin/env python3
"""Processa o CSV bruto da bateria de simulacoes: valida, agrega
estatisticas (media, desvio e intervalo de confianca de 95% por
protocolo x area x velocidade) e gera os 4 graficos obrigatorios (PDR,
throughput, delay, jitter x velocidade), separando o cenario-base
(area=500m) da extensao do grupo (area=250m).

Uso:
    python3 scripts/analyze.py
    python3 scripts/analyze.py --input /outro/caminho/resultados.csv
"""
import argparse
import csv
import math
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
    "throughput_kbps": ("Throughput (kbps)", "throughput_vs_velocidade"),
    "atraso_medio_ms": ("Atraso medio (ms)", "delay_vs_velocidade"),
    "jitter_medio_ms": ("Jitter medio (ms)", "jitter_vs_velocidade"),
}
COLUNAS_OBRIGATORIAS = [
    "protocolo", "area_m", "velocidade_m_s", "execucao",
    "pacotes_enviados", "pacotes_recebidos", *METRICAS,
]

PROTOCOLOS = ["AODV", "OLSR", "DSDV"]
REPETICOES_ESPERADAS = 5

# Paleta Okabe-Ito (seguro para daltonismo), uma cor fixa por protocolo
CORES = {"AODV": "#0072B2", "OLSR": "#E69F00", "DSDV": "#009E73"}
MARCADORES = {"AODV": "o", "OLSR": "s", "DSDV": "^"}

# Quantil 0,975 da t de Student para 1..30 graus de liberdade (IC bilateral de 95%)
T_975 = [
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
]


def t_critico(graus_liberdade):
    """Quantil 0,975 da t de Student; acima de 30 g.l. usa a expansao de Cornish-Fisher."""
    if graus_liberdade <= len(T_975):
        return T_975[graus_liberdade - 1]
    z, n = 1.959964, graus_liberdade
    return (z + (z**3 + z) / (4 * n) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * n**2)
            + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * n**3))


def carregar_dados(caminho):
    if not caminho.exists():
        sys.exit(
            f"Nao encontrei {caminho}.\n"
            "Rode a bateria completa (./scripts/run_batch.sh) e confira se o CSV "
            "foi copiado para data/raw/ antes de rodar esta analise."
        )
    with open(caminho, newline="") as f:
        leitor = csv.DictReader(f)
        linhas = list(leitor)
        colunas = leitor.fieldnames or []
    if "throughput_mbps" in colunas:
        sys.exit(
            f"{caminho} foi gerado por uma versao antiga do simulador: o PDR do OLSR usa como "
            "denominador so os pacotes que acharam rota, o throughput e dividido pelo tempo "
            "errado e execucoes sem entrega gravam atraso/jitter = 0. Rode a bateria novamente."
        )
    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in colunas]
    if faltando:
        sys.exit(f"Colunas ausentes em {caminho}: {', '.join(faltando)}")
    return linhas


def validar(linhas, repeticoes_esperadas=REPETICOES_ESPERADAS):
    problemas = []
    contagem = defaultdict(int)
    enviados = defaultdict(set)
    sem_entrega = 0
    for r in linhas:
        chave = (r["protocolo"], r["area_m"], r["velocidade_m_s"])
        contagem[chave] += 1
        enviados[(r["area_m"], r["velocidade_m_s"], r["execucao"])].add(int(r["pacotes_enviados"]))
        pdr = float(r["pdr_percent"])
        if pdr < 0 or pdr > 100:
            problemas.append(f"PDR fora de 0-100 em {chave}, execucao {r['execucao']}: {pdr}")
        if int(r["pacotes_enviados"]) == 0:
            problemas.append(f"Nenhum pacote enviado em {chave}, execucao {r['execucao']}")
        if int(r["pacotes_recebidos"]) == 0:
            sem_entrega += 1

    # A aplicacao gera a mesma quantidade de pacotes em qualquer protocolo; se isso
    # variar, o denominador do PDR nao e comparavel entre os protocolos.
    for (area, velocidade, execucao), valores in sorted(enviados.items()):
        if len(valores) > 1:
            problemas.append(
                f"pacotes_enviados difere entre protocolos em area={area}, vel={velocidade}, "
                f"execucao {execucao}: {sorted(valores)}"
            )

    for chave, n in contagem.items():
        if repeticoes_esperadas is not None and n != repeticoes_esperadas:
            problemas.append(f"{chave} tem {n} execucoes (esperado {repeticoes_esperadas})")

    if problemas:
        print(f"Avisos de validacao ({len(problemas)}):")
        for p in problemas:
            print(f"  - {p}")
    else:
        print("Validacao ok: nenhum problema encontrado.")
    if sem_entrega:
        print(f"{sem_entrega} execucoes sem nenhum pacote entregue: atraso e jitter ficam vazios "
              "(nan) e nao entram nas medias.")
    return linhas


def resumir(valores):
    """Media, desvio-padrao amostral, meia-largura do IC 95% e n, ignorando valores nan."""
    validos = [v for v in valores if not math.isnan(v)]
    n = len(validos)
    if n == 0:
        return math.nan, math.nan, math.nan, 0
    if n == 1:
        return validos[0], math.nan, math.nan, 1
    desvio = stdev(validos)
    return mean(validos), desvio, t_critico(n - 1) * desvio / math.sqrt(n), n


def agregar(linhas):
    grupos = defaultdict(list)
    for r in linhas:
        chave = (r["protocolo"], float(r["area_m"]), float(r["velocidade_m_s"]))
        grupos[chave].append(r)

    agregados = []
    for (protocolo, area, velocidade), grupo in sorted(grupos.items()):
        linha = {"protocolo": protocolo, "area_m": area, "velocidade_m_s": velocidade, "n": len(grupo)}
        for col in METRICAS:
            media, desvio, ic95, n = resumir([float(r[col]) for r in grupo])
            linha[f"{col}_media"] = media
            linha[f"{col}_desvio"] = desvio
            linha[f"{col}_ic95"] = ic95
            linha[f"{col}_n"] = n
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


def criar_figura(agregados, area_alvo, coluna, rotulo_area):
    rotulo_y = METRICAS[coluna][0]
    dados_area = [a for a in agregados if a["area_m"] == area_alvo]
    fig, ax = plt.subplots(figsize=(7, 5))
    for protocolo in PROTOCOLOS:
        pontos = sorted(
            (a["velocidade_m_s"], a[f"{coluna}_media"], a[f"{coluna}_ic95"])
            for a in dados_area
            if a["protocolo"] == protocolo
        )
        if not pontos:
            continue
        x = [p[0] for p in pontos]
        y = [p[1] for p in pontos]
        erro = [0.0 if math.isnan(p[2]) else p[2] for p in pontos]
        # Com poucas execucoes o IC pode ultrapassar limites fisicos (por exemplo,
        # PDR acima de 100%). Mantemos a media e recortamos apenas a barra.
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
        ax.set_ylim(0, 105)
    ax.legend(title="Barras: IC 95%")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig


def gerar_graficos(agregados, area_alvo, sufixo, rotulo_area):
    if not any(a["area_m"] == area_alvo for a in agregados):
        print(f"Sem dados para area={area_alvo} m, pulando graficos '{sufixo}'.")
        return

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    for coluna, (_, nome_base) in METRICAS.items():
        fig = criar_figura(agregados, area_alvo, coluna, rotulo_area)
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
