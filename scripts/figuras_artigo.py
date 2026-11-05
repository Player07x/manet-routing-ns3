#!/usr/bin/env python3
"""Gera a figura do artigo (PDR, throughput, atraso e jitter x velocidade;
uma linha de paineis por cenario) a partir de
data/processed/resultados-agregados.csv, em article/figuras/.

Uso:
    python3 scripts/analyze.py          # gera o CSV agregado
    python3 scripts/figuras_artigo.py
"""
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import CENARIOS, METRICAS, PROCESSED_DIR, REPO_DIR, desenhar_metrica  # noqa: E402

SAIDA = REPO_DIR / "article" / "figuras"
ROTULOS = {
    "pdr_percent": "PDR (%)",
    "throughput_mbps": "Throughput (Mbps)",
    "atraso_medio_ms": "Atraso médio (ms)",
    "jitter_medio_ms": "Jitter médio (ms)",
}

# Fonte serifada, proxima da Times do modelo SBC
plt.rcParams.update({
    "font.family": "STIXGeneral", "mathtext.fontset": "stix", "font.size": 7.5,
    "axes.labelsize": 7.5, "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7.5,
})


def carregar_agregados():
    caminho = PROCESSED_DIR / "resultados-agregados.csv"
    if not caminho.exists():
        sys.exit(f"Nao encontrei {caminho}. Rode antes: python3 scripts/analyze.py")
    with open(caminho, newline="") as f:
        linhas = list(csv.DictReader(f))
    numericas = lambda v: v if v in ("AODV", "OLSR", "DSDV") else float(v)  # noqa: E731
    return [{k: numericas(v) for k, v in linha.items()} for linha in linhas]


def main():
    agregados = carregar_agregados()
    SAIDA.mkdir(parents=True, exist_ok=True)
    fig, eixos = plt.subplots(len(CENARIOS), len(METRICAS), figsize=(6.3, 4.0), layout="constrained")
    letras = iter("abcdefgh")
    for linha, (area, _, _) in enumerate(CENARIOS):
        for coluna, metrica in enumerate(METRICAS):
            ax = eixos[linha, coluna]
            desenhar_metrica(ax, agregados, area, metrica, tamanho_marcador=3.5)
            ax.set_title(f"({next(letras)}) {ROTULOS[metrica]} — {area:g} m", fontsize=7.5)
            ax.set_ylabel("")
            ax.set_xlabel("Velocidade (m/s)" if linha == len(CENARIOS) - 1 else "")
    alcas, rotulos = [], []
    for ax in eixos.flat:  # a linha de carga ofertada so existe nos paineis de throughput
        for alca, rotulo in zip(*ax.get_legend_handles_labels()):
            if rotulo not in rotulos:
                alcas.append(alca)
                rotulos.append(rotulo)
    repeticoes = "/".join(sorted({f"{linha['n']:g}" for linha in agregados}))
    fig.legend(alcas, rotulos, loc="outside lower center", ncol=4, frameon=False,
               title=f"Média de {repeticoes} repetições; barras de erro: IC 95%", title_fontsize=7.5)
    destino = SAIDA / "metricas_artigo.pdf"
    fig.savefig(destino)
    plt.close(fig)
    print(f"Figura salva: {destino}")


if __name__ == "__main__":
    main()
