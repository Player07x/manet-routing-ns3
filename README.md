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

### Bateria completa (todas as combinações, ~20–22 minutos)

```bash
nohup ./ns3 run scratch/manet-routing > bateria.log 2>&1 &
```

Roda automaticamente **3 protocolos × 2 áreas × 4 velocidades × 5 repetições = 120 execuções**, uma atrás da outra, em um único comando. `nohup ... &` roda em segundo plano gravando o progresso em `bateria.log`, então a simulação continua mesmo se o terminal for fechado — acompanhe com `tail -f bateria.log`.

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

Depois que a bateria terminar, copie o resultado para dentro do repositório e versione:

```bash
cp ~/ns-3.48/resultados-aodv-olsr-dsdv.csv <caminho-deste-repo>/data/raw/
cd <caminho-deste-repo>
git add data/raw/resultados-aodv-olsr-dsdv.csv
git commit -m "Adiciona resultados da bateria completa (120 execucoes)"
git push
```

## Estrutura do repositório

```
manet-routing-ns3/
├── README.md
├── src/
│   └── manet-routing.cc
├── data/        — CSV bruto e processado (pendente)
├── figures/     — gráficos obrigatórios (pendente)
└── article/     — artigo final em PDF, modelo SBC (pendente)
```

## Status

- [x] Simulação (AODV, OLSR, DSDV) implementada e testada
- [ ] Bateria completa de execuções (120 runs)
- [ ] Análise estatística e gráficos
- [ ] Artigo científico

## Grupo

João Antonio André Barbosa Camilo e João Pedro Neto — Redes Móveis, IFG Câmpus Inhumas, 2026/2.
