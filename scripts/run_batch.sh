#!/usr/bin/env bash
# Builda e roda, em paralelo, a bateria completa usada no artigo
# (3 protocolos x 2 areas x 4 velocidades x 100 repeticoes = 2400 execucoes,
# 120 s cada, alcance de 250 m, seed 1, ARP permanente) e grava os resultados em data/raw/.
#
# Uso:
#   ./scripts/run_batch.sh
#
# Para rodar em segundo plano, sobrevivendo ao fechamento do terminal:
#   nohup ./scripts/run_batch.sh > run_batch.log 2>&1 &
#
# Ajuste NS3_DIR e WORKERS conforme a maquina (ex. WORKERS=8 ./scripts/run_batch.sh).

set -euo pipefail

NS3_DIR="${NS3_DIR:-$HOME/ns-3.48}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKERS="${WORKERS:-4}"
SIM_TIME=120
N_RUNS=100
ALCANCE=250
SEED=1
ARP=permanente

if [ ! -x "$NS3_DIR/ns3" ]; then
    echo "Nao encontrei uma instalacao do ns-3 em $NS3_DIR (defina NS3_DIR)." >&2
    exit 1
fi

if pgrep -f "build/scratch/.*manet-routing" > /dev/null 2>&1; then
    echo "Ja existe uma simulacao manet-routing rodando — aborte antes de rodar de novo (evita corromper o CSV)." >&2
    exit 1
fi

echo "Rodando a bateria completa em $WORKERS processos (3 protocolos x 2 areas x 4 velocidades x $N_RUNS repeticoes, ${SIM_TIME} s)..."
python3 "$REPO_DIR/scripts/run_batch_parallel.py" --ns3-dir "$NS3_DIR" --workers "$WORKERS" \
    --simTime "$SIM_TIME" --nRuns "$N_RUNS" --alcance "$ALCANCE" --seed "$SEED" --arp "$ARP"

echo "Pronto: resultados em $REPO_DIR/data/raw/"
echo "Proximo passo: python3 scripts/analyze.py"
