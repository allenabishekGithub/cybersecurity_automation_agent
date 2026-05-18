"""
retriever.py
------------
Wraps the FAISS vector store with a small, stable interface so the rest
of the code doesn't depend on LangChain's retriever specifics.
"""

from __future__ import annotations

from typing import List

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document


class RetrieverManager:
    """Thin wrapper around a FAISS store that exposes a stable retrieval interface.

    Decouples the rest of the application from LangChain's retriever API so
    that the underlying vector store can be swapped without touching callers.

    Args:
        vectorstore: A populated ``FAISS`` instance produced by
            ``IngestionManager.build_index``.
        top_k: Number of chunks to return per query.
    """

    def __init__(self, vectorstore: FAISS, top_k: int = 4) -> None:
        self.vectorstore = vectorstore
        self.top_k = top_k

    def retrieve(self, query: str) -> List[Document]:
        """Return the top-k most similar chunks for *query*.

        Args:
            query: Standalone question string (should already be
                contextualised by ``RAGAgent._contextualize``).

        Returns:
            List of up to ``top_k`` ``Document`` objects ordered by
            descending similarity.
        """
        return self.vectorstore.similarity_search(query, k=self.top_k)

    @staticmethod
    def format_context(docs: List[Document]) -> str:
        """Render retrieved chunks as a numbered, source-tagged context block.

        Each chunk is prefixed with ``[N] filename (p.X)`` so the LLM can
        attribute answers to specific sources.

        Args:
            docs: Documents returned by ``retrieve``.

        Returns:
            A single string suitable for insertion into the system prompt.
        """
        parts = []
        for i, d in enumerate(docs, start=1):
            src = d.metadata.get("source", "unknown")
            page = d.metadata.get("page")
            tag = f"[{i}] {src}" + (f" (p.{page + 1})" if isinstance(page, int) else "")
            parts.append(f"{tag}\n{d.page_content}")
        return "\n\n---\n\n".join(parts)

    @staticmethod
    def format_sources(docs: List[Document]) -> str:
        """Build a deduplicated, comma-separated citation string for the UI.

        Args:
            docs: Documents returned by ``retrieve``.

        Returns:
            E.g. ``"report.pdf (p.3), notes.txt"`` — empty string if *docs*
            is empty.
        """
        seen: dict[str, None] = {}
        for d in docs:
            src = d.metadata.get("source", "unknown")
            page = d.metadata.get("page")
            label = src + (f" (p.{page + 1})" if isinstance(page, int) else "")
            seen[label] = None
        return ", ".join(seen)
