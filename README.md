# MANET + ns-3 — Avaliação de Mobilidade e Roteamento (AODV × OLSR × DSDV)

Trabalho da disciplina **Redes Móveis** (2026/2) — Bacharelado em Engenharia de Software, IFG Câmpus Inhumas. Avalia como diferentes níveis de mobilidade afetam o desempenho dos protocolos de roteamento **AODV**, **OLSR** e **DSDV** em uma MANET (*Mobile Ad hoc NETwork*), simulada no ns-3.

## Pergunta de pesquisa

Como diferentes níveis de mobilidade afetam o desempenho dos protocolos AODV, OLSR e DSDV em uma rede MANET?

## Ambiente

- ns-3 **3.48**
- Linux, g++ com suporte a C++17, CMake
- Módulos ns-3 usados: `core`, `network`, `internet`, `applications`, `wifi`, `mobility`, `aodv`, `olsr`, `dsdv`, `flow-monitor`

## Compilação

Copie `src/manet-routing.cc` para a pasta `scratch/` de uma instalação do ns-3 3.48, depois:

```bash
cd <caminho-do-ns-3.48>
./ns3 build manet-routing
```

## Execução

### Bateria completa — forma recomendada (um comando só)

```bash
./scripts/run_batch.sh
```

Builda, roda as **3 protocolos × 2 áreas × 4 velocidades × 5 repetições = 120 execuções** (~20–22 minutos) e já copia o resultado para `data/raw/` dentro deste repositório. Para rodar em segundo plano, sobrevivendo ao fechamento do terminal:

```bash
nohup ./scripts/run_batch.sh > run_batch.log 2>&1 &
```

O script recusa rodar se já houver uma simulação em andamento, para evitar dois processos escrevendo no mesmo CSV ao mesmo tempo.

### Bateria completa — passo a passo manual (equivalente)

```bash
cd <caminho-do-ns-3.48>
nohup ./ns3 run scratch/manet-routing > bateria.log 2>&1 &
# depois de terminar:
cp <caminho-do-ns-3.48>/resultados-aodv-olsr-dsdv.csv <caminho-deste-repo>/data/raw/
```

### Execução reduzida (teste rápido)

```bash
./ns3 run "scratch/manet-routing --simTime=20 --nRuns=1"
```

### Parâmetros disponíveis

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `--nNodes` | 20 | Número de nós |
| `--simTime` | 120 | Tempo de simulação (s) |
| `--nRuns` | 5 | Repetições por combinação |

## Cenário

| Parâmetro | Valor |
|---|---|
| Tecnologia | IEEE 802.11 em modo Ad Hoc |
| Nós | 20 |
| Área | 500×500 m (cenário-base) e 250×250 m (variável experimental extra do grupo — densidade de nós) |
| Mobilidade | `RandomWaypointMobilityModel` |
| Velocidades | 1, 5, 10, 20 m/s |
| Protocolos | AODV, OLSR, DSDV |
| Aplicação | UDP, 4 fluxos simultâneos (100 pacotes de 512 B cada, 1 pacote/s por fluxo) |
| Repetições | 5 por combinação |
| Aleatoriedade | Seed mestre fixa (`1`), número de *run* variável a cada repetição — determinístico e reprodutível |

> Os resultados com área = 500 m são o experimento-base (comparação oficial entre os 3 protocolos); os resultados com área = 250 m são a extensão experimental do grupo.

## Métricas coletadas

Throughput (Mbps), Packet Delivery Ratio — PDR (%), atraso médio fim a fim (ms) e jitter médio (ms), calculadas via `FlowMonitor` a partir dos 4 fluxos UDP da aplicação.

## Saídas

A execução grava `resultados-aodv-olsr-dsdv.csv` na pasta onde o `./ns3 run` foi executado (raiz da instalação do ns-3) — **não** dentro deste repositório, já que o simulador só roda de dentro de uma árvore ns-3 instalada. Uma linha por execução:

```
protocolo,area_m,velocidade_m_s,execucao,pacotes_enviados,pacotes_recebidos,pdr_percent,atraso_medio_ms,throughput_mbps,jitter_medio_ms
```

`./scripts/run_batch.sh` já copia o CSV para `data/raw/` automaticamente. Depois disso, versione:

```bash
git add data/raw/resultados-aodv-olsr-dsdv.csv
git commit -m "Adiciona resultados da bateria completa (120 execucoes)"
git push
```

## Análise e gráficos

```bash
python3 -m venv .venv && source .venv/bin/activate   # uma vez só
pip install -r requirements.txt                       # uma vez só
python3 scripts/analyze.py
```

Lê `data/raw/resultados-aodv-olsr-dsdv.csv`, valida (PDR fora de 0–100%, execuções sem pacote enviado, contagem de repetições diferente de 5), salva as estatísticas agregadas (média e desvio padrão por protocolo × área × velocidade) em `data/processed/resultados-agregados.csv`, e gera em `figures/` os 4 gráficos obrigatórios — PDR, throughput, delay e jitter × velocidade, uma série por protocolo com barras de erro — separados entre cenário-base (500 m) e extensão do grupo (250 m).

## Estrutura do repositório

```
manet-routing-ns3/
├── README.md
├── requirements.txt
├── src/
│   └── manet-routing.cc
├── scripts/
│   ├── run_batch.sh      — builda, roda a bateria completa e copia o CSV para data/raw/
│   └── analyze.py        — valida, agrega e gera os gráficos a partir de data/raw/
├── data/
│   ├── raw/               — CSV bruto (pendente até rodar a bateria)
│   └── processed/         — estatísticas agregadas (gerado por analyze.py)
├── figures/               — gráficos obrigatórios (gerado por analyze.py)
└── article/               — artigo final em PDF, modelo SBC (pendente)
```

## Status

- [x] Simulação (AODV, OLSR, DSDV) implementada e testada
- [ ] Bateria completa de execuções (120 runs)
- [ ] Análise estatística e gráficos
- [ ] Artigo científico

## Grupo

João Antonio André Barbosa Camilo e João Pedro Neto — Redes Móveis, IFG Câmpus Inhumas, 2026/2.
