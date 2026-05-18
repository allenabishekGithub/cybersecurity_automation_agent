"""
ingestion.py
------------
IngestionManager handles everything from raw uploaded file bytes to a
queryable FAISS vector store: writing temp files, loading them with the
right LangChain loader, chunking, embedding, and indexing.

Designed to be re-callable: each `build_index(...)` call replaces the
previous index, so the user can swap documents mid-session.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable, List, Tuple

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


class IngestionManager:
    """Build a FAISS vector index from in-memory uploaded files.

    Handles the full pipeline from raw file bytes to a queryable FAISS store:
    temp-file I/O, document loading, chunking, embedding, and indexing.
    Calling ``build_index`` again replaces the previous index in place, so
    the user can swap document sets mid-session without creating a new instance.

    Args:
        api_key: OpenAI API key used to call the embedding model.
        embed_model: OpenAI embedding model name.
        chunk_size: Maximum character length of each text chunk.
        chunk_overlap: Character overlap between consecutive chunks to
            preserve context across chunk boundaries.
    """

    def __init__(
        self,
        api_key: str,
        embed_model: str = "text-embedding-3-small",
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
    ) -> None:
        self.api_key = api_key
        self.embed_model = embed_model
        self.embeddings = OpenAIEmbeddings(
            model=embed_model, api_key=api_key
        )
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    # ---- public API ------------------------------------------------------

    def build_index(self, uploads: Iterable[Tuple[str, bytes]]) -> Tuple[FAISS, dict]:
        """Ingest uploaded files and return a populated FAISS store plus stats.

        Args:
            uploads: Iterable of ``(filename, file_bytes)`` pairs. Only
                ``.pdf`` and ``.txt`` files are processed; others are skipped.

        Returns:
            A tuple of ``(vectorstore, stats)`` where *stats* is a dict with
            keys ``files``, ``raw_documents``, ``chunks``,
            ``embedding_model``, and ``skipped_files``.

        Raises:
            ValueError: If no readable content is found, or chunking produces
                zero chunks.
        """
        docs, skipped_files = self._load_all(uploads)
        if not docs:
            details = f" Skipped: {', '.join(skipped_files)}" if skipped_files else ""
            raise ValueError(
                "No readable content found in the uploaded files." + details
            )

        chunks = self.splitter.split_documents(docs)
        if not chunks:
            raise ValueError("Documents loaded but produced zero chunks.")

        vectorstore = FAISS.from_documents(chunks, self.embeddings)
        stats = {
            "files": len({d.metadata.get("source", "?") for d in docs}),
            "raw_documents": len(docs),
            "chunks": len(chunks),
            "embedding_model": self.embed_model,
            "skipped_files": skipped_files,
        }
        return vectorstore, stats

    # ---- internals -------------------------------------------------------

    def _load_all(self, uploads: Iterable[Tuple[str, bytes]]) -> Tuple[List[Document], List[str]]:
        docs: List[Document] = []
        skipped: List[str] = []
        for filename, blob in uploads:
            ext = Path(filename).suffix.lower()
            if ext not in {".pdf", ".txt"}:
                skipped.append(f"{filename} (unsupported file type)")
                continue
            try:
                loaded = self._load_one(filename, blob, ext)
            except Exception as e:
                skipped.append(f"{filename} ({type(e).__name__}: {e})")
                continue

            if not loaded:
                skipped.append(f"{filename} (no readable content)")
                continue

            for d in loaded:
                # Override with the original filename so citations look clean.
                d.metadata["source"] = filename
            docs.extend(loaded)
        return docs, skipped

    @staticmethod
    def _load_one(filename: str, blob: bytes, ext: str) -> List[Document]:
        # LangChain loaders want a real path on disk.
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=ext
        ) as tmp:
            tmp.write(blob)
            tmp_path = tmp.name

        try:
            if ext == ".pdf":
                return PyPDFLoader(tmp_path).load()
            return TextLoader(tmp_path, encoding="utf-8").load()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
