#!/usr/bin/env bash
# Builda, roda a bateria completa (120 execucoes) e copia o resultado
# para dentro do repositorio, em data/raw/.
#
# Uso:
#   ./scripts/run_batch.sh
#
# Para rodar em segundo plano, sobrevivendo ao fechamento do terminal:
#   nohup ./scripts/run_batch.sh > run_batch.log 2>&1 &
#
# Ajuste NS3_DIR se a instalacao do ns-3 usada pelo grupo estiver em
# outro caminho (ex.: NS3_DIR=~/ns-3.48 ./scripts/run_batch.sh).

set -euo pipefail

NS3_DIR="${NS3_DIR:-$HOME/ns-3.48}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTADO="resultados-aodv-olsr-dsdv.csv"

if [ ! -x "$NS3_DIR/ns3" ]; then
    echo "Nao encontrei uma instalacao do ns-3 em $NS3_DIR (defina NS3_DIR)." >&2
    exit 1
fi

if pgrep -f "build/scratch/.*manet-routing" > /dev/null 2>&1; then
    echo "Ja existe uma simulacao manet-routing rodando — aborte antes de rodar de novo (evita corromper o CSV)." >&2
    exit 1
fi

echo "Copiando codigo-fonte para dentro da instalacao do ns-3..."
cp "$REPO_DIR/src/manet-routing.cc" "$NS3_DIR/scratch/manet-routing.cc"

echo "Compilando..."
(cd "$NS3_DIR" && ./ns3 build manet-routing)

echo "Rodando a bateria completa (3 protocolos x 2 areas x 4 velocidades x 5 repeticoes = 120 execucoes, ~20-22 min)..."
(cd "$NS3_DIR" && ./ns3 run scratch/manet-routing)

echo "Copiando resultado para dentro do repositorio..."
mkdir -p "$REPO_DIR/data/raw"
cp "$NS3_DIR/$RESULTADO" "$REPO_DIR/data/raw/"

echo "Pronto: $REPO_DIR/data/raw/$RESULTADO"
echo "Proximo passo: python3 scripts/analyze.py"
