#!/usr/bin/env python3
"""Processa os CSVs brutos da bateria de simulacoes: valida, agrega
estatisticas (media, desvio e intervalo de confianca de 95% por
protocolo x area x velocidade) e gera os 4 graficos obrigatorios (PDR,
throughput, delay, jitter x velocidade), separando o cenario-base
(area=500m) da extensao do grupo (area=750m).

Uso:
    python3 scripts/analyze.py
    python3 scripts/analyze.py --input /outro/caminho/resultados.csv --input-fluxos /outro/caminho/fluxos.csv
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
DEFAULT_FLOWS_CSV = REPO_DIR / "data" / "raw" / "resultados-por-fluxo.csv"
PROCESSED_DIR = REPO_DIR / "data" / "processed"
FIGURES_DIR = REPO_DIR / "figures"

# Uma metrica -> (rotulo do eixo Y, nome base do arquivo de figura)
METRICAS = {
    "pdr_percent": ("PDR (%)", "pdr_vs_velocidade"),
    "throughput_mbps": ("Throughput (Mbps)", "throughput_vs_velocidade"),
    "atraso_medio_ms": ("Atraso médio (ms)", "delay_vs_velocidade"),
    "jitter_medio_ms": ("Jitter médio (ms)", "jitter_vs_velocidade"),
}
COLUNAS_OBRIGATORIAS = [
    "protocolo", "area_m", "velocidade_m_s", "seed", "execucao",
    "pacotes_enviados", "pacotes_recebidos", *METRICAS,
    "atraso_p50_ms", "atraso_p95_ms", "pacotes_tx_ip", "descartes_mac",
]
FLUXOS_POR_EXECUCAO = 4

PROTOCOLOS = ["AODV", "OLSR", "DSDV"]
# 500 m: cenario-base exigido; 750 m: extensao do grupo
CENARIOS = [
    (500.0, "area500_base", "cenário-base, 500×500 m"),
    (750.0, "area750_extensao", "extensão do grupo, 750×750 m"),
]
REPETICOES_ESPERADAS = 100

# Paleta Okabe-Ito (seguro para daltonismo), uma cor fixa por protocolo
CORES = {"AODV": "#0072B2", "OLSR": "#E69F00", "DSDV": "#009E73"}
MARCADORES = {"AODV": "o", "OLSR": "s", "DSDV": "^"}
# Deslocamento horizontal (m/s) de cada protocolo, para as barras de erro nao se sobreporem
DESLOCAMENTO = {"AODV": -0.3, "OLSR": 0.0, "DSDV": 0.3}

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
            "Rode a bateria completa (./scripts/run_batch.sh) antes de rodar esta analise."
        )
    with open(caminho, newline="") as f:
        leitor = csv.DictReader(f)
        linhas = list(leitor)
        colunas = leitor.fieldnames or []
    if "seed" not in colunas:
        sys.exit(
            f"{caminho} foi gerado por uma versao antiga do simulador (sem a coluna seed e com "
            "metricas calculadas de outra forma). Rode a bateria novamente."
        )
    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in colunas]
    if faltando:
        sys.exit(f"Colunas ausentes em {caminho}: {', '.join(faltando)}")
    return linhas


def carregar_fluxos(caminho):
    if not caminho.exists():
        print(f"Aviso: {caminho} nao existe; a validacao por fluxo foi pulada.")
        return None
    with open(caminho, newline="") as f:
        return list(csv.DictReader(f))


def chave_execucao(r):
    return (r["protocolo"], r["area_m"], r["velocidade_m_s"], r["seed"], r["execucao"])


def validar(linhas, fluxos, repeticoes_esperadas=REPETICOES_ESPERADAS):
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

    fluxos_sem_recepcao = []
    if fluxos is not None:
        # Os fluxos de cada execucao precisam somar exatamente os totais da execucao
        por_execucao = defaultdict(list)
        for f in fluxos:
            por_execucao[chave_execucao(f)].append(f)
        for r in linhas:
            grupo = por_execucao.get(chave_execucao(r), [])
            if len(grupo) != FLUXOS_POR_EXECUCAO:
                problemas.append(f"{chave_execucao(r)} tem {len(grupo)} fluxos (esperado {FLUXOS_POR_EXECUCAO})")
                continue
            for coluna in ("pacotes_enviados", "pacotes_recebidos"):
                if sum(int(f[coluna]) for f in grupo) != int(r[coluna]):
                    problemas.append(f"{chave_execucao(r)}: soma de {coluna} dos fluxos difere do total")
        fluxos_sem_recepcao = [f for f in fluxos if int(f["pacotes_recebidos"]) == 0]

    if problemas:
        print(f"Avisos de validacao ({len(problemas)}):")
        for p in problemas:
            print(f"  - {p}")
    else:
        print("Validacao ok: nenhum problema encontrado.")
    if sem_entrega:
        print(f"{sem_entrega} execucoes sem nenhum pacote entregue: atraso e jitter ficam vazios "
              "(nan) e nao entram nas medias.")
    if fluxos_sem_recepcao:
        print(f"{len(fluxos_sem_recepcao)} fluxos sem nenhum pacote recebido:")
        for f in fluxos_sem_recepcao:
            print(f"  - {f['protocolo']} area={f['area_m']} vel={f['velocidade_m_s']} "
                  f"execucao={f['execucao']} fluxo={f['fluxo']} ({f['origem']} -> {f['destino']})")
    elif fluxos is not None:
        print("Todos os fluxos receberam pelo menos um pacote.")
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
        # Metricas auxiliares da discussao (sem grafico): percentis do atraso e causas de perda
        auxiliares = {
            "atraso_p50_ms": [float(r["atraso_p50_ms"]) for r in grupo],
            "atraso_p95_ms": [float(r["atraso_p95_ms"]) for r in grupo],
            "perda_mac_percent": [100.0 * int(r["descartes_mac"]) / int(r["pacotes_enviados"]) for r in grupo],
            "perda_origem_percent": [
                100.0 * (int(r["pacotes_enviados"]) - int(r["pacotes_tx_ip"])) / int(r["pacotes_enviados"])
                for r in grupo
            ],
        }
        for col, valores in auxiliares.items():
            media, desvio, ic95, n = resumir(valores)
            linha[f"{col}_media"] = media
            linha[f"{col}_ic95"] = ic95
        # Com pacotes de tamanho fixo, throughput / PDR e a carga ofertada pela aplicacao
        cargas = [
            float(r["throughput_mbps"]) / (float(r["pdr_percent"]) / 100.0)
            for r in grupo
            if float(r["pdr_percent"]) > 0
        ]
        linha["carga_ofertada_mbps"] = mean(cargas) if cargas else math.nan
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


def desenhar_metrica(ax, agregados, area_alvo, coluna, tamanho_marcador=7):
    """Desenha uma serie por protocolo (media e IC 95%) no eixo ax."""
    dados_area = [a for a in agregados if a["area_m"] == area_alvo]
    topo = 0.0
    for protocolo in PROTOCOLOS:
        pontos = sorted(
            (a["velocidade_m_s"], a[f"{coluna}_media"], a[f"{coluna}_ic95"])
            for a in dados_area
            if a["protocolo"] == protocolo
        )
        if not pontos:
            continue
        x = [p[0] + DESLOCAMENTO[protocolo] for p in pontos]
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
        topo = max([topo] + [v + e for v, e in zip(y, erro_superior) if not math.isnan(v)])
        ax.errorbar(
            x, y, yerr=[erro_inferior, erro_superior],
            marker=MARCADORES[protocolo], markersize=tamanho_marcador, linewidth=2,
            capsize=4, label=protocolo, color=CORES[protocolo],
        )

    if coluna == "throughput_mbps":
        cargas = [a["carga_ofertada_mbps"] for a in dados_area if not math.isnan(a["carga_ofertada_mbps"])]
        if cargas:
            carga = mean(cargas)
            ax.axhline(carga, color="0.4", linestyle="--", linewidth=1.2, label="Carga ofertada")
            topo = max(topo, carga)
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)

    velocidades = sorted({a["velocidade_m_s"] for a in dados_area})
    ax.set_xticks(velocidades, [f"{v:g}" for v in velocidades])
    ax.set_xlabel("Velocidade (m/s)")
    ax.set_ylabel(METRICAS[coluna][0])
    # Margem acima do maior valor para os marcadores nao ficarem cortados na borda
    if coluna == "pdr_percent":
        ax.set_ylim(0, 105)
    else:
        ax.set_ylim(0, topo * 1.12 if topo > 0 else 1.0)
    ax.grid(True, alpha=0.3)


def criar_figura(agregados, area_alvo, coluna, rotulo_area):
    fig, ax = plt.subplots(figsize=(7, 5.4), layout="constrained")
    desenhar_metrica(ax, agregados, area_alvo, coluna)
    ax.set_title(f"{METRICAS[coluna][0]} × velocidade — {rotulo_area}")
    # Legenda fora da area do grafico, para nunca cobrir pontos ou barras de erro
    fig.legend(loc="outside lower center", ncol=4, title="Barras de erro: IC 95%", frameon=False)
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
    parser.add_argument("--input", type=Path, default=DEFAULT_RAW_CSV, help="CSV bruto (uma linha por execucao)")
    parser.add_argument("--input-fluxos", type=Path, default=DEFAULT_FLOWS_CSV, help="CSV bruto por fluxo")
    parser.add_argument(
        "--expected-runs", type=int, default=REPETICOES_ESPERADAS,
        help="Numero esperado de repeticoes por combinacao (use 0 para nao validar)",
    )
    args = parser.parse_args()

    linhas = carregar_dados(args.input)
    fluxos = carregar_fluxos(args.input_fluxos)
    repeticoes_esperadas = args.expected_runs if args.expected_runs > 0 else None
    linhas = validar(linhas, fluxos, repeticoes_esperadas)
    agregados = agregar(linhas)
    salvar_processado(agregados)
    for area, sufixo, rotulo in CENARIOS:
        gerar_graficos(agregados, area_alvo=area, sufixo=sufixo, rotulo_area=rotulo)


if __name__ == "__main__":
    main()
