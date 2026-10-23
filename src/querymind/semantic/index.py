"""The schema index in pgvector (3.3): every schema document with its embedding.

    ensure_index(admin_conn, reader="analyst_ro")      # once, as admin (scripts/build_index.py)
    upsert_documents(admin_conn, docs, embeddings)     # embeds only new or changed documents
    hits = search(ro_conn, embeddings.embed_query(question), limit=10)
    hits = keyword_search(ro_conn, question, limit=10)  # Postgres full-text search (3.4)

It lives in its own schema, `querymind`, not in `public`: the SQL validator only allows
`public` tables, so SQL written by the LLM can never read (or learn about) the index. The
read-only role may SELECT from it for searching; only the admin role writes to it.

Vector search is an exact scan: `ORDER BY embedding <=> query` (cosine distance) over a few
dozen rows. An approximate index (HNSW) only pays off at many thousands of rows.

Keyword search (3.4) uses a `tsvector` column Postgres keeps up to date itself (GENERATED):
the document's words, stemmed ("categories" → "categori") and without stopwords, with the
table/metric name weighted higher than the description. A GIN index makes it fast.
"""

from collections.abc import Sequence

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict

from querymind.llm.embeddings import DIMENSIONS, EmbeddingProvider
from querymind.semantic.documents import SchemaDoc

INDEX_SCHEMA = "querymind"
INDEX_TABLE = "schema_docs"


class SchemaIndexError(Exception):
    """SCHEMA_RETRIEVAL=on, but the index can't be used (missing, empty, wrong model)."""


class IndexHit(BaseModel):
    """One search result: which document, the table it points to, and how good a match."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    table: str
    # Vector search: cosine similarity (1 = same direction, 0 = unrelated). Keyword search:
    # ts_rank (higher = more, and more important, words matched). Only compare within one list.
    score: float


def ensure_index(conn: psycopg.Connection, *, reader: str, table: str = INDEX_TABLE) -> None:
    """Create the extension, schema and table if missing, and let `reader` search it.

    Needs the admin role (CREATE EXTENSION). Safe to run again.
    """
    name = sql.Identifier(INDEX_SCHEMA, table)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(INDEX_SCHEMA)))
    conn.execute(
        sql.SQL(
            """CREATE TABLE IF NOT EXISTS {} (
                id text PRIMARY KEY,
                kind text NOT NULL,
                name text NOT NULL,
                table_name text NOT NULL,
                text text NOT NULL,
                hash text NOT NULL,
                model text NOT NULL,
                embedding vector({}) NOT NULL
            )"""
        ).format(name, sql.Literal(DIMENSIONS))
    )
    # Keyword search (3.4). ADD COLUMN IF NOT EXISTS: a 3.3 index gets it in place, and
    # Postgres fills it for every existing row. Name words ("order_details" → order, detail)
    # weigh A, the description B: matching the table's own name counts more.
    conn.execute(
        sql.SQL(
            """ALTER TABLE {} ADD COLUMN IF NOT EXISTS words tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('english', name), 'A')
                || setweight(to_tsvector('english', text), 'B')
            ) STORED"""
        ).format(name)
    )
    conn.execute(
        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} USING gin (words)").format(
            sql.Identifier(f"{table}_words_idx"), name
        )
    )
    role = sql.Identifier(reader)
    conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(INDEX_SCHEMA), role)
    )
    conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(name, role))


def stored_hashes(
    conn: psycopg.Connection, *, table: str = INDEX_TABLE
) -> dict[str, tuple[str, str]]:
    """id → (text hash, embedding model) of every stored document."""
    query = sql.SQL("SELECT id, hash, model FROM {}").format(sql.Identifier(INDEX_SCHEMA, table))
    return {id_: (hash_, model) for id_, hash_, model in conn.execute(query)}


def stale_documents(
    docs: Sequence[SchemaDoc], stored: dict[str, tuple[str, str]], *, model: str
) -> list[SchemaDoc]:
    """Documents that are new, changed, or were embedded by another model."""
    return [doc for doc in docs if stored.get(doc.id) != (doc.hash, model)]


def upsert_documents(
    conn: psycopg.Connection,
    docs: Sequence[SchemaDoc],
    embeddings: EmbeddingProvider,
    *,
    table: str = INDEX_TABLE,
) -> tuple[int, int]:
    """Bring the index in line with `docs`: embed what changed, delete what's gone.

    Returns (embedded, deleted). One embedding call for all changed documents, none if
    nothing changed: rebuilding is free when the schema and definitions didn't move.
    """
    name = sql.Identifier(INDEX_SCHEMA, table)
    stored = stored_hashes(conn, table=table)
    changed = stale_documents(docs, stored, model=embeddings.name)
    if changed:
        vectors = embeddings.embed_documents([doc.text for doc in changed])
        with conn.cursor() as cur:
            cur.executemany(
                sql.SQL(
                    """INSERT INTO {} (id, kind, name, table_name, text, hash, model, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name,
                        table_name = EXCLUDED.table_name, text = EXCLUDED.text,
                        hash = EXCLUDED.hash, model = EXCLUDED.model,
                        embedding = EXCLUDED.embedding"""
                ).format(name),
                [
                    (d.id, d.kind, d.name, d.table, d.text, d.hash, embeddings.name, to_vector(v))
                    for d, v in zip(changed, vectors, strict=True)
                ],
            )
    gone = sorted(set(stored) - {doc.id for doc in docs})
    if gone:
        conn.execute(sql.SQL("DELETE FROM {} WHERE id = ANY(%s)").format(name), (gone,))
    return len(changed), len(gone)


class IndexStatus(BaseModel):
    """Can the index be searched, and is anything about it worth a warning?"""

    model_config = ConfigDict(frozen=True)

    usable: bool
    message: str = ""  # why it can't be used, or what is out of date ("" = all fine)


def index_status(
    conn: psycopg.Connection, docs: Sequence[SchemaDoc], *, model: str, table: str = INDEX_TABLE
) -> IndexStatus:
    """Check the index before the app relies on it (once, at the first question).

    Unusable: missing, empty, or embedded with a different model (vectors from two models
    can't be compared, e.g. an index built with --fake-embeddings searched with Gemini).
    Usable with a warning: some documents changed since it was built (stale, not wrong).
    """
    rebuild = "run: uv run python scripts/build_index.py"
    try:
        stored = stored_hashes(conn, table=table)
    except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName):
        return IndexStatus(usable=False, message=f"there is no schema index yet; {rebuild}")
    except psycopg.errors.InsufficientPrivilege:
        return IndexStatus(
            usable=False, message=f"the read-only role can't read the index; {rebuild}"
        )
    if not stored:
        return IndexStatus(usable=False, message=f"the schema index is empty; {rebuild}")
    models = sorted({m for _, m in stored.values()})
    if models != [model]:
        return IndexStatus(
            usable=False,
            message=f"the index was embedded with {', '.join(models)}, not {model}; {rebuild}",
        )
    stale = stale_documents(docs, stored, model=model)
    gone = set(stored) - {doc.id for doc in docs}
    if stale or gone:
        return IndexStatus(
            usable=True,
            message=f"{len(stale) + len(gone)} schema documents changed since the index was "
            f"built; {rebuild}",
        )
    return IndexStatus(usable=True)


def search(
    conn: psycopg.Connection, query_vector: Sequence[float], *, limit: int, table: str = INDEX_TABLE
) -> list[IndexHit]:
    """The `limit` documents closest to the question's vector, closest first."""
    query = sql.SQL(
        """SELECT id, kind, table_name, 1 - (embedding <=> %(q)s::vector) AS similarity
        FROM {} ORDER BY embedding <=> %(q)s::vector LIMIT %(limit)s"""
    ).format(sql.Identifier(INDEX_SCHEMA, table))
    rows = conn.execute(query, {"q": to_vector(query_vector), "limit": limit})
    return [IndexHit(id=i, kind=k, table=t, score=float(s)) for i, k, t, s in rows]


# Words about the database itself, not the business: every document says "Table ..." and many
# say "one row per ...", so they'd match everything. Dropped from the question like stopwords
# (as stems: to_tsvector('english', 'tables rows') = 'tabl' 'row').
SCHEMA_WORDS = ("tabl", "row", "column", "record", "field", "databas")

# The question's words, stemmed, stopwords dropped, joined with OR ('categori' | 'revenu').
# Not websearch_to_tsquery: it ANDs every word, and no description holds all the words of a
# question. Each word is quoted (quote_literal), so nothing in the question is tsquery syntax.
_KEYWORD_QUERY = """
WITH q AS (
    SELECT string_agg(quote_literal(word), ' | ')::tsquery AS query
    FROM unnest(tsvector_to_array(to_tsvector('english', %(question)s))) AS word
    WHERE word <> ALL(%(schema_words)s)
)
SELECT id, kind, table_name, ts_rank(words, q.query) AS score
FROM {}, q
WHERE words @@ q.query
ORDER BY score DESC, id
LIMIT %(limit)s
"""


def keyword_search(
    conn: psycopg.Connection, question: str, *, limit: int, table: str = INDEX_TABLE
) -> list[IndexHit]:
    """The `limit` documents sharing the most (and most important) words with the question.

    No embedding call: this works when the embedding API is down, and it catches exact words
    ("category", a column name) that vector search can rank too low. A question of only
    stopwords ("How many are there?") matches nothing and returns [].
    """
    query = sql.SQL(_KEYWORD_QUERY).format(sql.Identifier(INDEX_SCHEMA, table))
    params = {"question": question, "limit": limit, "schema_words": list(SCHEMA_WORDS)}
    rows = conn.execute(query, params)
    return [IndexHit(id=i, kind=k, table=t, score=float(s)) for i, k, t, s in rows]


def to_vector(vector: Sequence[float]) -> str:
    """pgvector's text form, '[0.1,0.2,...]': sent as text and cast, so no extra library."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"
