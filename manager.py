"""
manager.py
----------
AppManager composes every component. Its __init__ instantiates the
ingestion manager and memory (which both need only the API key).
The retriever and the RAG agent are built lazily by `index_documents()`
once the user has uploaded files, because they need a populated FAISS
store to bind to.

The Streamlit UI keeps one AppManager in session state and calls:
    manager.index_documents(uploads)   # once, after upload
    manager.ask(question)              # per turn
"""

from __future__ import annotations

from typing import Iterable, Iterator, List, Tuple

from langchain_core.documents import Document

from components.ingestion import IngestionManager
from components.memory import LayeredMemory
from components.network_executor import NetworkExecutor
from components.rag_agent import RAGAgent
from components.retriever import RetrieverManager


class AppManager:
    """Top-level facade that owns and wires every component.

    Lifecycle:

    1. Instantiate with an API key — creates ``IngestionManager`` and
       ``LayeredMemory``.  No network calls are made yet.
    2. Call ``index_documents()`` after the user uploads files — embeds the
       documents and builds ``RetrieverManager`` and ``RAGAgent``.
    3. Call ``ask()`` per turn to get a ``(stream, docs)`` pair, then
       ``commit_turn()`` once the stream is fully consumed.

    ``is_ready()`` returns ``False`` until step 2 completes, and the
    Streamlit UI uses it to gate the chat input.

    Args:
        api_key: OpenAI API key forwarded to all components.
        chat_model: Chat model for reasoning and summarisation.
        embed_model: Embedding model for document and query vectors.
        top_k: Chunks retrieved per query.
        window_size: Recent turns passed verbatim to the model each turn.
        summarize_after: Turn count at which old turns are folded into the
            rolling summary.
        mininet_api_url: Base URL of the Mininet simulator API used for
            implementation requests.
    """

    def __init__(
        self,
        api_key: str,
        *,
        chat_model: str = "gpt-4o-mini",
        embed_model: str = "text-embedding-3-small",
        top_k: int = 4,
        window_size: int = 4,
        summarize_after: int = 6,
        mininet_api_url: str = "http://mininet-sim:8080",
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required.")

        self.api_key = api_key
        self.chat_model = chat_model
        self.top_k = top_k

        # Always-available components
        self.ingestion = IngestionManager(api_key, embed_model=embed_model)
        self.memory = LayeredMemory(
            api_key=api_key,
            chat_model=chat_model,
            window_size=window_size,
            summarize_after=summarize_after,
        )
        self.network_executor = NetworkExecutor(mininet_api_url)

        # Set after index_documents() is called
        self.retriever: RetrieverManager | None = None
        self.agent: RAGAgent | None = None
        self.index_stats: dict | None = None

    # ---- lifecycle -------------------------------------------------------

    def index_documents(self, uploads: Iterable[Tuple[str, bytes]]) -> dict:
        """Ingest uploaded files and (re)build the retriever and agent.

        Safe to call multiple times — each call replaces the previous index
        and resets the agent.  Memory is *not* cleared automatically; call
        ``memory.clear()`` first if a fresh conversation is desired.

        Args:
            uploads: Iterable of ``(filename, file_bytes)`` pairs.

        Returns:
            Stats dict from ``IngestionManager.build_index`` (keys:
            ``files``, ``raw_documents``, ``chunks``, ``embedding_model``,
            ``skipped_files``).
        """
        vectorstore, stats = self.ingestion.build_index(uploads)
        self.retriever = RetrieverManager(vectorstore, top_k=self.top_k)
        self.agent = RAGAgent(
            api_key=self.api_key,
            retriever=self.retriever,
            memory=self.memory,
            chat_model=self.chat_model,
        )
        self.index_stats = stats
        return stats

    def is_ready(self) -> bool:
        """Return ``True`` once ``index_documents`` has been called successfully."""
        return self.agent is not None

    # ---- per-turn --------------------------------------------------------

    def ask(self, question: str) -> Tuple[Iterator[str], List[Document]]:
        """Start a RAG turn and return a streaming response.

        Args:
            question: Raw user question.

        Returns:
            ``(token_iterator, retrieved_docs)`` — see ``RAGAgent.stream_answer``.

        Raises:
            RuntimeError: If called before ``index_documents``.
        """
        if self.agent is None:
            raise RuntimeError(
                "No index yet. Upload at least one PDF/TXT first."
            )
        return self.agent.stream_answer(question)

    def commit_turn(self, question: str, answer: str, sources: str) -> None:
        """Record a completed turn in memory.

        Must be called *after* the token stream from ``ask()`` is fully
        consumed, so memory stores the complete answer text.

        Args:
            question: The user's question.
            answer: The fully assembled assistant response.
            sources: Citation string produced by ``RetrieverManager.format_sources``.
        """
        self.memory.add_turn(question, answer, sources)

    # ---- introspection ---------------------------------------------------

    def memory_stats(self) -> dict:
        """Return memory layer statistics for the sidebar display."""
        return self.memory.stats()

    # ---- network change execution ---------------------------------------

    @staticmethod
    def is_implementation_request(question: str) -> bool:
        q = question.lower()
        intent_tokens = (
            "implement",
            "apply",
            "configure",
            "enforce",
            "harden",
            "block",
            "deny",
            "disable",
            "restrict",
            "allow",
        )
        return any(token in q for token in intent_tokens)

    def implement_network_change(self, question: str) -> dict:
        """Apply a network change request to the Mininet simulator pod."""
        return self.network_executor.implement(question)

    def topology_state(self) -> dict:
        """Fetch current Mininet topology and policy state."""
        return self.network_executor.topology()
