# Retrieval benchmark - pgvector vs ChromaDB vs hybrid

- Documents: 212 (212 chunks); labelled queries: 20 + 1 scope-leak probe(s)
- Embeddings: `HashingEmbeddings`; stores: pgvector (in-memory stand-in), chroma (in-memory stand-in)

| Approach | Recall@5 | MRR | Top-1 authoritative | Scope leaks | Avg latency (ms) |
|---|---|---|---|---|---|
| pgvector | 0.88 | 0.85 | 0.70 | 0 | 0.4 |
| chroma | 0.88 | 0.85 | 0.70 | 0 | 0.5 |
| hybrid | 0.90 | 0.93 | 0.75 | 0 | 0.7 |

How to read this: Recall@5 = did we find the right documents; MRR = how high the first right one ranked; Top-1 authoritative = was the #1 result the source we should plan on (e.g. contract v3, not the superseded v2). Scope leaks must be 0 for every approach because the filter runs inside each store's query.
