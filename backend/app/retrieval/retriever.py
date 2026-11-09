"""Hybrid knowledge-base search with LlamaIndex.

Two retrievers over the org's store, vector (cosine similarity on mistral-embed vectors) and
keyword (Postgres full-text search), merged with Reciprocal Rank Fusion: every chunk scores
sum(1 / (60 + rank)) across the two lists, so chunks that rank well in both rise to the top.
Ranks are fused rather than scores because cosine similarity and ts_rank are on different scales.
"""

import uuid

from llama_index.core import QueryBundle, VectorStoreIndex
from llama_index.core.llms import MockLLM
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import NodeWithScore
from llama_index.core.vector_stores.types import VectorStoreQueryMode

from app.retrieval.store import get_embed_model, org_store

CANDIDATES = 20  # how many chunks each retriever contributes before fusion


def org_retriever(org_id: uuid.UUID, top_k: int = 5) -> QueryFusionRetriever:
    """A hybrid retriever that can only see this org's chunks (the store enforces it)."""
    index = VectorStoreIndex.from_vector_store(org_store(org_id), embed_model=get_embed_model())
    vector = index.as_retriever(similarity_top_k=CANDIDATES)
    keyword = index.as_retriever(
        vector_store_query_mode=VectorStoreQueryMode.TEXT_SEARCH,
        similarity_top_k=CANDIDATES,
        sparse_top_k=CANDIDATES,
    )
    return QueryFusionRetriever(
        [vector, keyword],
        mode=FUSION_MODES.RECIPROCAL_RANK,
        similarity_top_k=top_k,
        # 1 = search with the user's query only. More would ask an LLM to invent extra
        # queries; the MockLLM is there only because the class insists on having one.
        num_queries=1,
        llm=MockLLM(),
        use_async=True,
    )


async def search_knowledge(org_id: uuid.UUID, query: str, top_k: int = 5) -> list[NodeWithScore]:
    """The org's most relevant knowledge-base chunks for `query`, best first.

    Each node's metadata has `document_title` and `section`, which answers must cite.
    """
    # Embedded once here and shared by both retrievers, so a search costs one embedding call.
    embedding = await get_embed_model().aget_query_embedding(query)
    return await org_retriever(org_id, top_k).aretrieve(QueryBundle(query_str=query, embedding=embedding))
