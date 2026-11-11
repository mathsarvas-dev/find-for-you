# Benchmarks

## Embeddings locais (bge-m3) — risco de desempenho local (spec, seção 10)

Comando: `python scripts/bench_embeddings.py` (200 textos de ~300 palavras, truncados a 512 tokens, batch padrão do sentence-transformers).

| Item | Valor |
|---|---|
| CPU | 13th Gen Intel Core i7-1355U (12 threads), 15,7 GB RAM |
| GPU | nenhuma (sem NVIDIA) |
| torch | 2.15.0+cpu, device `cpu` |
| Carga do modelo (+ 1ª chamada) | 24,0 s (pesos já em cache; 1º download ~2,3 GB) |
| Vazão | 0,40 textos/s (200 textos em 499 s) |
| Memória | RSS atual 2258 MB, pico 2948 MB |

Observações: em CPU, textos de ~300 palavras (frequentemente > 512 tokens e truncados) custam ~2,5 s cada. Vetorizar um lote grande de vagas é viável em segundo plano (worker), mas não de forma síncrona na API. Os textos do benchmark são palavras sintéticas aleatórias, que geram mais sub-tokens que texto natural, então o número é um pior caso; texto real tende a ser mais rápido. Uma GPU ou um modelo menor reduziria muito o tempo.
