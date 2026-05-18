"""
rag_agent.py
------------
RAGAgent ties the pieces together for a single user turn:

    user question
        │
        ▼
    contextualise against memory (so follow-ups become standalone queries)
        │
        ▼
    retrieve top-k chunks from the FAISS store via RetrieverManager
        │
        ▼
    build a grounded prompt: [memory context] + [retrieved chunks] + [question]
        │
        ▼
    stream tokens from the chat model

The agent itself is stateless — memory and retriever are injected.
"""

from __future__ import annotations

from typing import Iterator, List, Tuple

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from .memory import LayeredMemory
from .retriever import RetrieverManager


REFUSAL = "I don't have enough information in the provided documents."

CONTEXTUALIZE_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "Given the chat history and the latest user message, rewrite the user "
     "message as a standalone question that can be understood without the "
     "history. Do NOT answer it. If the latest message is already standalone, "
     "return it unchanged."),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])

ANSWER_SYSTEM = (
    "You are a domain support assistant. Answer the user's question using "
    "ONLY the context below. If the answer is not contained in the context, "
    f"reply with exactly: \"{REFUSAL}\"\n"
    "Do not use outside knowledge. Be concise. When you use information "
    "from the context, mention the source filename in parentheses.\n\n"
    "Context:\n{context}"
)


class RAGAgent:
    """End-to-end RAG turn handler with streaming output.

    Orchestrates a single user turn in three steps:

    1. **Contextualise** — rewrite the user's question as a standalone query
       using recent chat history, so follow-ups resolve correctly.
    2. **Retrieve** — fetch the top-k chunks from the FAISS index.
    3. **Answer** — stream a grounded response using only the retrieved
       context; refuse with ``REFUSAL`` if the answer isn't present.

    The agent is stateless: memory and retriever are injected at construction
    and shared with the rest of the application via ``AppManager``.

    Args:
        api_key: OpenAI API key.
        retriever: Populated ``RetrieverManager`` bound to the current index.
        memory: Shared ``LayeredMemory`` instance.
        chat_model: Model used for both contextualisation and answering.
        temperature: Sampling temperature. Defaults to 0 for determinism.
    """

    def __init__(
        self,
        api_key: str,
        retriever: RetrieverManager,
        memory: LayeredMemory,
        chat_model: str = "gpt-4o-mini",
        temperature: float = 0.0,
    ) -> None:
        self.retriever = retriever
        self.memory = memory
        # One LLM for contextualisation, one for answering — same config,
        # but keeps the streaming separable.
        self.llm = ChatOpenAI(
            model=chat_model, temperature=temperature, api_key=api_key,
            streaming=True,
        )
        self.contextualizer = ChatOpenAI(
            model=chat_model, temperature=0, api_key=api_key,
        )

    # ---- public API ------------------------------------------------------

    def stream_answer(self, question: str) -> Tuple[Iterator[str], List[Document]]:
        """Run a full RAG turn and return a streaming response.

        The caller is responsible for consuming the iterator (e.g. via
        ``st.write_stream``) before calling ``AppManager.commit_turn`` — the
        full answer text is only available once the stream is exhausted.

        Args:
            question: Raw user question (may be a follow-up).

        Returns:
            A tuple of ``(token_iterator, retrieved_docs)``.  *token_iterator*
            yields string chunks as they arrive from the model.
            *retrieved_docs* are the chunks used to ground the answer, for
            citation rendering.
        """
        standalone = self._contextualize(question)
        docs = self.retriever.retrieve(standalone)
        context = self.retriever.format_context(docs)

        messages = [SystemMessage(content=ANSWER_SYSTEM.format(context=context))]
        messages.extend(self.memory.as_prompt_context())
        messages.append(HumanMessage(content=question))

        def token_iter() -> Iterator[str]:
            for chunk in self.llm.stream(messages):
                # chunk.content can be empty for some control frames.
                if chunk.content:
                    yield chunk.content

        return token_iter(), docs

    # ---- internals -------------------------------------------------------

    def _contextualize(self, question: str) -> str:
        history = self.memory.as_prompt_context()
        if not history:
            return question
        msgs = CONTEXTUALIZE_PROMPT.format_messages(
            chat_history=history, input=question
        )
        try:
            return self.contextualizer.invoke(msgs).content.strip() or question
        except Exception:
            # If reformulation fails, fall back to the raw question.
            return question
