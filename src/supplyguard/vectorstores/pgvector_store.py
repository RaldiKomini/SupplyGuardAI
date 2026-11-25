"""pgvector store: the baseline document index and the approved-incident-memory index.

Two separate tables (the assignment asks for separate collections):
  - evidence_documents        -> contracts, notices, e-mails, procedures ...
  - evidence_incident_memories -> approved resolution summaries

Connection: DATABASE_URL, e.g. postgresql://supplyguard:supplyguard@postgres:5432/supplyguard
(the Postgres service itself is run by Elpiniki's docker-compose, using the
pgvector/pgvector image so the `vector` extension is available).

Access scope, supplier and document type are filtered in the SQL WHERE clause,
so rows the user may not see are never returned by the database.
"""

from __future__ import annotations

import os

from supplyguard.retrieval.models import DocumentChunk
from supplyguard.vectorstores.embeddings import embedding_dim

DOCUMENTS_TABLE = "evidence_documents"
MEMORIES_TABLE = "evidence_incident_memories"

COLUMNS = [
    "chunk_id",
    "source_id",
    "doc_type",
    "title",
    "version",
    "effective_date",
    "access_scope",
    "supplier_id",
    "status",
    "security_flags",
]


# On re-index, EVERY column is refreshed from the new chunk (provenance included: title,
# version, effective date, scope, status ...), so a stored row can never keep stale
# provenance next to new text.
UPDATE_ALL = ", ".join(f"{c} = EXCLUDED.{c}" for c in [*COLUMNS[1:], "text", "embedding"])


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


class PgVectorStore:
    def __init__(self, embeddings, table: str = DOCUMENTS_TABLE, dsn: str | None = None):
        import psycopg  # imported here so the rest of the package works without it

        if table not in (DOCUMENTS_TABLE, MEMORIES_TABLE):
            raise ValueError(f"unknown table {table}")  # table names are never user input
        self.name = "pgvector"
        self.embeddings = embeddings
        self.table = table
        self.dim = embedding_dim(embeddings)
        self.conn = psycopg.connect(dsn or os.environ["DATABASE_URL"], autocommit=True)
        self._create_table()

    def _create_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    chunk_id       TEXT PRIMARY KEY,
                    source_id      TEXT NOT NULL,
                    doc_type       TEXT NOT NULL,
                    title          TEXT,
                    version        INTEGER,
                    effective_date TEXT,
                    access_scope   TEXT NOT NULL,
                    supplier_id    TEXT,
                    status         TEXT,
                    security_flags TEXT,
                    text           TEXT NOT NULL,
                    embedding      vector({self.dim}) NOT NULL
                )""")
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_scope_idx ON {self.table} (access_scope)"
            )

    def add(self, chunks: list[DocumentChunk]) -> None:
        vectors = self.embeddings.embed_documents([c.text for c in chunks])
        with self.conn.cursor() as cur:
            for chunk, vec in zip(chunks, vectors, strict=True):
                meta = chunk.metadata()
                cur.execute(
                    f"""
                    INSERT INTO {self.table} ({", ".join(COLUMNS)}, text, embedding)
                    VALUES ({", ".join(["%s"] * len(COLUMNS))}, %s, %s::vector)
                    ON CONFLICT (chunk_id) DO UPDATE SET {UPDATE_ALL}""",
                    [meta[c] for c in COLUMNS] + [chunk.text, _vector_literal(vec)],
                )

    def search(self, query, allowed_scopes, k=8, supplier_id=None, doc_types=None):
        if not allowed_scopes:
            return []
        qvec = _vector_literal(self.embeddings.embed_query(query))
        where, params = ["access_scope = ANY(%s)"], [list(allowed_scopes)]
        if supplier_id:
            where.append("supplier_id = %s")
            params.append(supplier_id)
        if doc_types:
            where.append("doc_type = ANY(%s)")
            params.append(list(doc_types))
        sql = f"""
            SELECT {", ".join(COLUMNS)}, text, 1 - (embedding <=> %s::vector) AS similarity
            FROM {self.table}
            WHERE {" AND ".join(where)}
            ORDER BY embedding <=> %s::vector
            LIMIT %s"""
        with self.conn.cursor() as cur:
            cur.execute(sql, [qvec] + params + [qvec, k])
            rows = cur.fetchall()
        results = []
        for row in rows:
            meta = dict(zip(COLUMNS, row[: len(COLUMNS)], strict=True))
            results.append((DocumentChunk.from_metadata(row[len(COLUMNS)], meta), float(row[-1])))
        return results

    def count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {self.table}")
            return cur.fetchone()[0]

    def all_chunks(self) -> list[DocumentChunk]:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(COLUMNS)}, text FROM {self.table}")
            rows = cur.fetchall()
        return [
            DocumentChunk.from_metadata(row[len(COLUMNS)], dict(zip(COLUMNS, row, strict=False)))
            for row in rows
        ]
