"""Embedding models.

Configured with the EMBEDDINGS_PROVIDER environment variable:
  - "hash" (default): a small local embedder with no model download and no API key.
    Deterministic, so tests and the benchmark are reproducible offline. It captures
    word overlap, not meaning, so it is a baseline, not a production choice.
  - "openai": LangChain's OpenAIEmbeddings (model from EMBEDDINGS_MODEL,
    default text-embedding-3-small). Needs OPENAI_API_KEY.
  - "huggingface": LangChain's HuggingFaceEmbeddings (default
    sentence-transformers/all-MiniLM-L6-v2), runs locally.

Every embedder exposes the LangChain Embeddings methods embed_documents / embed_query,
so pgvector, Chroma and the in-memory store all use the same object.
"""

from __future__ import annotations

import hashlib
import math
import os
import re

TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lower-case tokens; keeps IDs like 'mc-17', 'po-2026-9001', 's001' as one token."""
    return TOKEN.findall(text.lower())


class HashingEmbeddings:
    """Feature hashing of words and word pairs into a fixed-size vector."""

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _bucket(self, feature: str) -> tuple[int, float]:
        h = int(hashlib.md5(feature.encode()).hexdigest(), 16)  # md5: stable across runs
        return h % self.dim, (1.0 if (h >> 64) % 2 == 0 else -1.0)

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        features = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        for f in features:
            i, sign = self._bucket(f)
            vec[i] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def get_embeddings():
    provider = os.getenv("EMBEDDINGS_PROVIDER", "hash").lower()
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small"))
    if provider == "huggingface":
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(
            model_name=os.getenv("EMBEDDINGS_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        )
    return HashingEmbeddings()


def embedding_dim(embeddings) -> int:
    return getattr(embeddings, "dim", None) or len(embeddings.embed_query("dimension probe"))
