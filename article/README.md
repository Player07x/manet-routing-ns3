# article/

Artigo final no modelo SBC (até 5 páginas, incluindo referências), com citações e referências segundo a ABNT (NBR 10520 e NBR 6023, via pacote abnTeX2).

| Arquivo | Conteúdo |
|---|---|
| `artigo.pdf` | Versão compilada do artigo |
| `artigo.tex` | Código-fonte LaTeX |
| `referencias.bib` | Referências (DOIs conferidos no Crossref) |
| `sbc-template.sty` | Estilo oficial dos artigos da SBC |
| `figuras/metricas_artigo.pdf` | Figura 1, gerada por `scripts/figuras_artigo.py` |

Para compilar localmente (Tectonic baixa os pacotes LaTeX necessários):

```bash
cd article && tectonic artigo.tex
```

No Overleaf, envie a pasta `article/` inteira e compile `artigo.tex` (XeLaTeX ou pdfLaTeX).
