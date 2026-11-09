"""The knowledge-base vector store (LlamaIndex PGVectorStore on Supabase's Postgres + pgvector).

Tenant isolation: every org gets its own store object, and each one adds
`WHERE metadata_->>'org_id' = :org_id` (a bound parameter) to every query it runs, vector and
keyword alike, through LlamaIndex's `customize_query_fn` hook. So a retriever built on an org's
store can only ever see that org's chunks; there is no filter for a caller to forget.

All orgs share one table; org_id lives in the JSON metadata, because LlamaIndex owns the
table's columns.
"""

import uuid
from functools import lru_cache
from typing import Any

from llama_index.embeddings.mistralai import MistralAIEmbedding
from llama_index.vector_stores.postgres import PGVectorStore
from sqlalchemy import Select, create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.retrieval import KB_TABLE_NAME

EMBEDDING_MODEL = "mistral-embed"
EMBEDDING_DIMENSIONS = 1024


@lru_cache
def get_embed_model() -> MistralAIEmbedding:
    api_key = get_settings().mistral_api_key
    if api_key is None:
        raise RuntimeError("MISTRAL_API_KEY is not set. Add it to .env (see .env.example).")
    return MistralAIEmbedding(
        model_name=EMBEDDING_MODEL, api_key=api_key.get_secret_value(), embed_batch_size=32
    )


@lru_cache
def kb_engines() -> tuple[Engine, AsyncEngine]:
    """Shared by every org's store. PGVectorStore uses the sync engine (psycopg2) to create its
    table and the async one (asyncpg) for queries."""
    url = get_settings().database_url
    sync_url = make_url(url).set(drivername="postgresql+psycopg2")
    return (
        create_engine(sync_url, pool_pre_ping=True),
        create_async_engine(url, pool_pre_ping=True),
    )


@lru_cache
def org_store(org_id: uuid.UUID) -> PGVectorStore:
    """The vector store as seen by one org. Every query it runs is limited to that org."""
    if not isinstance(org_id, uuid.UUID):
        raise TypeError("org_id must be a uuid.UUID from the server-side tenant context")
    org = str(org_id)

    # LlamaIndex calls this as fn(stmt, table, **kwargs) but types it as taking three
    # positional arguments; *_ accepts both.
    def only_this_org(stmt: Select[Any], table: Any, *_: Any, **__: Any) -> Select[Any]:
        return stmt.where(table.metadata_["org_id"].astext == org)

    engine, async_engine = kb_engines()
    return PGVectorStore(
        engine=engine,
        async_engine=async_engine,
        table_name=KB_TABLE_NAME,
        embed_dim=EMBEDDING_DIMENSIONS,
        # Adds a generated tsvector column with a GIN index, for the keyword half of hybrid search.
        hybrid_search=True,
        text_search_config="english",
        use_jsonb=True,
        # An index on metadata_->>'org_id', since every query filters on it.
        indexed_metadata_keys={("org_id", "text")},
        customize_query_fn=only_this_org,
    )
