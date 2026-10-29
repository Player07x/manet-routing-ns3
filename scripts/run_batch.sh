#!/usr/bin/env bash
# Builda, roda a bateria completa (120 execucoes) em paralelo e copia o
# resultado para dentro do repositorio, em data/raw/.
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

if [ ! -x "$NS3_DIR/ns3" ]; then
    echo "Nao encontrei uma instalacao do ns-3 em $NS3_DIR (defina NS3_DIR)." >&2
    exit 1
fi

if pgrep -f "build/scratch/.*manet-routing" > /dev/null 2>&1; then
    echo "Ja existe uma simulacao manet-routing rodando — aborte antes de rodar de novo (evita corromper o CSV)." >&2
    exit 1
fi

echo "Rodando a bateria completa em $WORKERS processos (3 protocolos x 2 areas x 4 velocidades x 5 repeticoes = 120 execucoes)..."
python3 "$REPO_DIR/scripts/run_batch_parallel.py" --ns3-dir "$NS3_DIR" --workers "$WORKERS"

echo "Pronto: $REPO_DIR/data/raw/resultados-aodv-olsr-dsdv.csv"
echo "Proximo passo: python3 scripts/analyze.py"
