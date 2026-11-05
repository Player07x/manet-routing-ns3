# data/

- `raw/` — saída da bateria completa (2.400 execuções; ver `README.md` na raiz):
  - `resultados-aodv-olsr-dsdv.csv` — uma linha por execução;
  - `resultados-por-fluxo.csv` — uma linha por fluxo de cada execução;
  - `execucoes.csv` — log de cada execução (parâmetros, versão do ns-3, commit, horários);
  - `metadados.json` — ambiente e parâmetros da bateria.
- `processed/resultados-agregados.csv` — média, desvio-padrão, IC 95% e *n* por protocolo × área × velocidade, gerado por `scripts/analyze.py` e usado nos gráficos de `../figures/` e na figura do artigo.
