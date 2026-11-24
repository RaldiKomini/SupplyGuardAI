"""BM25 keyword search, written out in full so it is easy to explain.

Why we need it next to vector search: IDs such as 'MC-17', 'PO-2026-9002' or
'S004' carry no "meaning" for an embedding model, but an exact keyword match on
them is very strong evidence.

BM25 score of a document for a query = sum over query words of
    idf(word) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc_len / avg_len))
where tf = how often the word appears in the document and idf rewards rare words.
"""

from __future__ import annotations

import math
from collections import Counter

from supplyguard.retrieval.models import DocumentChunk
from supplyguard.vectorstores.embeddings import tokenize


class BM25Index:
    name = "bm25"

    def __init__(self, chunks: list[DocumentChunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.term_freqs = [Counter(tokenize(c.text)) for c in chunks]
        self.lengths = [sum(tf.values()) for tf in self.term_freqs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if chunks else 0.0
        doc_freq = Counter(term for tf in self.term_freqs for term in tf)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t, df in doc_freq.items()}

    def search(self, query, allowed_scopes, k=8, supplier_id=None, doc_types=None):
        terms = tokenize(query)
        scored = []
        for i, chunk in enumerate(self.chunks):
            c = chunk.citation
            if c.access_scope not in allowed_scopes:  # filter before scoring
                continue
            if supplier_id and c.supplier_id != supplier_id:
                continue
            if doc_types and c.doc_type not in doc_types:
                continue
            tf, length, score = self.term_freqs[i], self.lengths[i], 0.0
            for t in terms:
                if t in tf:
                    f = tf[t]
                    score += (
                        self.idf[t]
                        * f
                        * (self.k1 + 1)
                        / (f + self.k1 * (1 - self.b + self.b * length / self.avg_len))
                    )
            if score > 0:
                scored.append((chunk, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]
