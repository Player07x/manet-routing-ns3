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

Builda, roda as **3 protocolos × 2 áreas × 4 velocidades × 5 repetições = 120 execuções** em processos independentes e já grava o resultado em `data/raw/`. O padrão usa 4 processos simultâneos; ajuste com `WORKERS` conforme a capacidade da máquina:

```bash
WORKERS=8 ./scripts/run_batch.sh
```

O paralelismo é feito por processos porque o `Simulator` do ns-3 mantém estado global dentro de cada processo e não é seguro executar várias simulações com `std::thread` no mesmo processo. Cada tarefa usa uma combinação de protocolo, área, velocidade e repetição, grava um CSV temporário próprio e só depois todos os resultados são combinados atomicamente no CSV final. As sementes continuam reprodutíveis (`seed=1`, `run=1..nRuns`).

Para executar diretamente e controlar mais parâmetros:

```bash
python3 scripts/run_batch_parallel.py --ns3-dir "$HOME/ns-3.48" \
  --workers 4 --nNodes 20 --simTime 120 --nRuns 5 --alcance 250
```

Para rodar em segundo plano, sobrevivendo ao fechamento do terminal:

```bash
WORKERS=4 nohup ./scripts/run_batch.sh > run_batch.log 2>&1 &
```

O script recusa rodar se já houver uma simulação em andamento, para evitar dois processos escrevendo no mesmo CSV ao mesmo tempo.

### Bateria completa — passo a passo manual (modo sequencial)

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
| `--alcance` | 250 | Alcance do rádio em metros (modelo de disco) |

## Cenário

| Parâmetro | Valor |
|---|---|
| Tecnologia | IEEE 802.11b em modo Ad Hoc |
| Rádio | Alcance fixo de 250 m (`RangePropagationLossModel`), valor clássico da literatura de MANET. O canal padrão do ns-3.48 (802.11ax, LogDistance) limitaria o alcance a ~51 m e deixaria o cenário de 500×500 m particionado |
| Nós | 20 |
| Área | 500×500 m (cenário-base) e 250×250 m (variável experimental extra do grupo — densidade de nós) |
| Mobilidade | `RandomWaypointMobilityModel` |
| Velocidades | 1, 5, 10, 20 m/s |
| Protocolos | AODV, OLSR, DSDV |
| Aplicação | UDP (echo), 4 fluxos simultâneos de 512 B a 1 pacote/s por fluxo, de 10 s até o fim da simulação; cada fluxo começa com um atraso aleatório de até 1 s para os quatro não transmitirem sempre no mesmo instante |
| Repetições | 5 por combinação |
| Aleatoriedade | Seed mestre fixa (`1`), número de *run* variável a cada repetição — determinístico e reprodutível |

> Os resultados com área = 500 m são o experimento-base (comparação oficial entre os 3 protocolos); os resultados com área = 250 m são a extensão experimental do grupo.

## Métricas coletadas

Calculadas a partir dos 4 fluxos UDP da aplicação (as respostas do echo não entram):

- **PDR (%)** — pacotes recebidos (`FlowMonitor`) ÷ pacotes gerados pela aplicação (trace `Tx` do cliente). O denominador é o mesmo nos três protocolos; o `txPackets` do `FlowMonitor` não serve, porque o OLSR descarta no socket os pacotes sem rota antes de eles chegarem à camada IP.
- **Throughput (kbps)** — bytes recebidos (com cabeçalhos IP/UDP) ÷ janela de tráfego (de 10 s até `simTime`). A carga ofertada é ≈ 17,3 kbps.
- **Atraso médio (ms)** e **jitter médio (ms)** — `delaySum` e `jitterSum` do `FlowMonitor` divididos pelo número de amostras. Execuções sem amostras gravam `nan` e ficam fora das médias.

## Saídas

A execução grava `resultados-aodv-olsr-dsdv.csv` na pasta onde o `./ns3 run` foi executado (raiz da instalação do ns-3) — **não** dentro deste repositório, já que o simulador só roda de dentro de uma árvore ns-3 instalada. Uma linha por execução:

```
protocolo,area_m,velocidade_m_s,execucao,pacotes_enviados,pacotes_recebidos,pdr_percent,atraso_medio_ms,throughput_kbps,jitter_medio_ms
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

### Interface visual opcional

Para executar a simulação, acompanhar o log, visualizar os resultados e exportar CSVs/ZIP pelo navegador:

```bash
pip install -r requirements.txt
streamlit run scripts/dashboard.py
```

Execute esses comandos a partir da pasta do repositório. No Windows, o dashboard chama o ns-3 dentro do WSL; no Ubuntu, ele executa o ns-3 diretamente no sistema local. A interface é uma camada separada: o `scripts/analyze.py` e os comandos tradicionais continuam funcionando sem ela. A simulação usa os parâmetros configuráveis de nós, tempo e repetições e mantém o cenário experimental atual (AODV, OLSR e DSDV; áreas 500/250 m; velocidades 1/5/10/20 m/s). No Ubuntu, informe a pasta local do ns-3 (por padrão, `~/ns-3.48`); no Windows, informe a pasta do ns-3 dentro do WSL.

O dashboard permite escolher o número de processos simultâneos, interromper a bateria pelo botão **Interromper simulação** e acompanha automaticamente o estado a cada dois segundos.

Lê `data/raw/resultados-aodv-olsr-dsdv.csv`, valida (PDR fora de 0–100%, execuções sem pacote enviado, `pacotes_enviados` diferente entre protocolos na mesma execução, contagem de repetições diferente de 5), salva as estatísticas agregadas (média, desvio padrão, meia-largura do intervalo de confiança de 95% pela t de Student e número de amostras válidas, por protocolo × área × velocidade) em `data/processed/resultados-agregados.csv`, e gera em `figures/` os 4 gráficos obrigatórios — PDR, throughput, delay e jitter × velocidade, uma série por protocolo com barras de erro de IC 95% — separados entre cenário-base (500 m) e extensão do grupo (250 m). CSVs gerados pela versão antiga do simulador (coluna `throughput_mbps`) são recusados, porque têm o PDR do OLSR, o throughput e o atraso/jitter calculados de forma incorreta.

Se a bateria usar outra quantidade de repetições, informe-a na validação, por exemplo:

```bash
python3 scripts/analyze.py --expected-runs 100
```

O valor `--expected-runs 0` desativa essa validação específica.

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
