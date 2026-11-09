"""Builds an org's knowledge base from its Markdown files.

Chunking: LlamaIndex's MarkdownNodeParser splits each document at its headings, so each
section becomes one chunk, and each FAQ question (a ### heading) is one chunk with its answer.
We then record the section's heading path, e.g. "Ordering > Can I change or cancel my order?",
which is what answers cite alongside the document title. A SentenceSplitter guards against an
over-long section; ours are all well under its limit.

What gets embedded is the chunk text prefixed with its document title and section, so a short
answer is still found by the topic it belongs to.
"""

import re
import uuid
from pathlib import Path

from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter
from llama_index.core.schema import BaseNode, MetadataMode, TextNode
from llama_index.core.vector_stores import MetadataFilter, MetadataFilters
from sqlalchemy import text

from app.retrieval import KB_DB_TABLE
from app.retrieval.store import get_embed_model, kb_engines, org_store

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# About 400 tokens; the largest section we have is about 160.
MAX_CHUNK_TOKENS = 400
# Kept for filtering and citing, but left out of the embedded text and the LLM's view.
_HIDDEN_KEYS = ["org_id", "document"]


def _read_documents(org_id: uuid.UUID, kb_dir: Path) -> list[Document]:
    documents = []
    for path in sorted(kb_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        title = _HEADING.match(content.splitlines()[0])
        if not title or len(title.group(1)) != 1:
            raise ValueError(f"{path}: the first line must be a '# Title'")
        documents.append(
            Document(
                text=content,
                metadata={"org_id": str(org_id), "document": path.stem, "document_title": title.group(2)},
            )
        )
    return documents


def build_nodes(org_id: uuid.UUID, kb_dir: Path) -> list[BaseNode]:
    sections = MarkdownNodeParser(include_prev_next_rel=False).get_nodes_from_documents(
        _read_documents(org_id, kb_dir)
    )
    nodes: list[BaseNode] = []
    for node in sections:
        heading_line, _, body = node.get_content(metadata_mode=MetadataMode.NONE).partition("\n")
        heading = _HEADING.match(heading_line)
        body = body.strip()
        if not heading or not body:
            continue  # a heading with nothing but subheadings under it, e.g. "## Ordering"
        level, title = len(heading.group(1)), heading.group(2)
        # header_path is "/Document title/Parent heading/"; drop the document title.
        parents = [part for part in node.metadata.pop("header_path", "").strip("/").split("/") if part][1:]
        section = " > ".join([*parents, title]) if level > 1 else "Introduction"
        nodes.append(
            TextNode(
                text=f"{title}\n\n{body}",
                metadata={**node.metadata, "section": section},
                excluded_embed_metadata_keys=_HIDDEN_KEYS,
                excluded_llm_metadata_keys=_HIDDEN_KEYS,
            )
        )

    nodes = SentenceSplitter(chunk_size=MAX_CHUNK_TOKENS, chunk_overlap=40)(nodes)
    for position, node in enumerate(nodes):
        # Stable ids, so a rebuild of unchanged content produces the same ids.
        node.id_ = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{org_id}/{node.metadata['document']}/{position}"))
    return nodes


async def rebuild_org_kb(org_id: uuid.UUID, kb_dir: Path) -> int:
    """Replaces the org's knowledge base with the contents of kb_dir. Returns the chunk count.

    Embeds first, so if the embedding API fails the old knowledge base is left untouched.
    """
    nodes = build_nodes(org_id, kb_dir)
    embeddings = await get_embed_model().aget_text_embedding_batch(
        [node.get_content(metadata_mode=MetadataMode.EMBED) for node in nodes]
    )
    for node, embedding in zip(nodes, embeddings, strict=True):
        node.embedding = embedding

    store = org_store(org_id)
    await store.adelete_nodes(
        filters=MetadataFilters(filters=[MetadataFilter(key="org_id", value=str(org_id))])
    )
    await store.async_add(nodes)
    return len(nodes)


async def lock_down_kb_table() -> None:
    """Row-level security on LlamaIndex's table, like every other table (see the initial
    migration). The default privileges set there already keep Supabase's REST API roles out."""
    _, async_engine = kb_engines()
    async with async_engine.begin() as conn:
        await conn.execute(text(f"ALTER TABLE {KB_DB_TABLE} ENABLE ROW LEVEL SECURITY"))
