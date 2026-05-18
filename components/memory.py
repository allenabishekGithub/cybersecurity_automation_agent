"""
memory.py
---------
LayeredMemory combines three classic memory strategies:

  1. Full transcript       — kept in Streamlit session state, shown in the UI.
  2. Rolling summary       — older turns are condensed by the LLM into a
                             running summary once the window overflows.
  3. Recent window         — the last N turns pass through verbatim.

When the agent builds a prompt it asks `as_prompt_context()`, which returns
the summary plus the verbatim window — never the full history. That keeps
token cost bounded for long sessions while preserving early context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI


SUMMARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You maintain a running summary of an ongoing conversation between a "
     "user and an assistant. Update the existing summary by folding in the "
     "new turns. Keep it concise (under ~200 words), factual, and focused "
     "on facts, decisions, and questions the user has asked. Do not invent "
     "details."),
    ("human",
     "Existing summary:\n{existing}\n\nNew turns to fold in:\n{new_turns}\n\n"
     "Return ONLY the updated summary."),
])


@dataclass
class Turn:
    user: str
    assistant: str
    sources: str = ""  # human-readable source list, for display only

    def to_messages(self) -> List[BaseMessage]:
        return [HumanMessage(content=self.user), AIMessage(content=self.assistant)]


@dataclass
class LayeredMemory:
    """Three-tier conversation memory that keeps token cost bounded.

    Layers (from oldest to newest):

    1. **Rolling summary** — once ``history`` exceeds ``summarize_after``
       turns, the oldest excess turns are condensed by the LLM into a
       running plain-text summary and dropped from ``history``.
    2. **Recent window** — the last ``window_size`` turns are kept verbatim
       and sent to the model on every request.
    3. **Full transcript** — kept by the UI in ``st.session_state.chat_log``.
       ``LayeredMemory`` stores only what is needed for prompting.

    When ``as_prompt_context()`` is called, it returns the summary (as a
    ``SystemMessage``) followed by the verbatim window — never the full
    history — so prompt length grows only up to a fixed ceiling.

    Args:
        api_key: OpenAI API key used by the summarisation LLM.
        chat_model: Model used for rolling summarisation.
        window_size: Number of most-recent turns passed verbatim to the model.
        summarize_after: Total turns threshold that triggers summarisation.
            Should be greater than ``window_size``; excess turns are folded
            into the summary.
    """

    api_key: str
    chat_model: str = "gpt-4o-mini"
    window_size: int = 4            # number of recent turns kept verbatim
    summarize_after: int = 6        # roll older turns into summary above this

    summary: str = ""
    history: List[Turn] = field(default_factory=list)
    total_turns: int = 0
    summarized_turns: int = 0
    _llm: "ChatOpenAI" = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._llm = ChatOpenAI(
            model=self.chat_model, temperature=0, api_key=self.api_key
        )

    # ---- public API ------------------------------------------------------

    def add_turn(self, user: str, assistant: str, sources: str = "") -> None:
        """Append a completed turn and trigger summarisation if needed.

        Args:
            user: The user's message text.
            assistant: The assistant's full response text (not a stream).
            sources: Human-readable citation string, stored for display only.
        """
        self.history.append(Turn(user=user, assistant=assistant, sources=sources))
        self.total_turns += 1
        self._maybe_summarize()

    def as_prompt_context(self) -> List[BaseMessage]:
        """Return the message list to prepend to the next LLM request.

        Shape: ``[SystemMessage(summary)]?`` + ``[last window_size turns]``.
        The summary message is omitted when no summarisation has occurred yet.
        """
        msgs: List[BaseMessage] = []
        if self.summary:
            msgs.append(SystemMessage(
                content=f"Summary of earlier conversation:\n{self.summary}"
            ))
        for turn in self.history[-self.window_size:]:
            msgs.extend(turn.to_messages())
        return msgs

    def clear(self) -> None:
        """Reset all memory layers. The index/documents are unaffected."""
        self.summary = ""
        self.history.clear()
        self.total_turns = 0
        self.summarized_turns = 0

    # ---- introspection helpers used by the UI sidebar --------------------

    def stats(self) -> dict:
        """Return a snapshot of memory state for display in the sidebar.

        Returns:
            Dict with keys:

            - ``total_turns`` — full turns recorded since the last ``clear()``.
            - ``in_window`` — turns included verbatim in the next prompt.
            - ``in_summary`` — total turns represented only by the rolling
              summary since the last ``clear()``.
            - ``summary_chars`` — character length of the current summary.
        """
        return {
            "total_turns": self.total_turns,
            "in_summary": self.summarized_turns if self.summary else 0,
            "in_window": min(self.window_size, len(self.history)),
            "summary_chars": len(self.summary),
        }

    # ---- internals -------------------------------------------------------

    def _maybe_summarize(self) -> None:
        """When history exceeds the threshold, roll everything outside the
        recent window into the running summary."""
        if len(self.history) <= self.summarize_after:
            return

        excess = len(self.history) - self.window_size
        if excess <= 0:
            return

        to_summarize = self.history[:excess]
        new_turns_text = "\n".join(
            f"User: {t.user}\nAssistant: {t.assistant}" for t in to_summarize
        )

        prompt = SUMMARY_PROMPT.format_messages(
            existing=self.summary or "(none yet)",
            new_turns=new_turns_text,
        )
        try:
            self.summary = self._llm.invoke(prompt).content.strip()
        except Exception:
            # If summarisation fails, leave older turns in place this round.
            return

        # Drop the summarised turns so we don't double-count them.
        self.history = self.history[excess:]
        self.summarized_turns += excess
