"""
app.py
------
Streamlit entrypoint for the RAG assistant.

Run with:
    streamlit run app.py

The flow has three gates:
    1. User pastes their OpenAI API key in the sidebar.
    2. User uploads at least one PDF or TXT; clicking "Build index"
       ingests and embeds them.
    3. User chats. Each turn streams tokens, shows citations, and
       updates the layered memory.
"""

from __future__ import annotations

import os

import streamlit as st

from manager import AppManager


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(page_title="RAG Domain Assistant", page_icon="📚",
                   layout="wide")
st.title("📚 RAG Domain Assistant")
st.caption("Upload your documents, then ask grounded questions about them. "
           "Streaming answers, layered memory, source citations.")


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

ss = st.session_state
ss.setdefault("api_key", os.environ.get("OPENAI_API_KEY", ""))
ss.setdefault("manager", None)              # AppManager instance
ss.setdefault("chat_log", [])               # [{role, content, sources}]
ss.setdefault("topology_state", None)       # mininet topology/policy snapshot


# ---------------------------------------------------------------------------
# Sidebar: API key, upload, memory stats, reset
# ---------------------------------------------------------------------------

with st.sidebar:
    st.subheader("🔑 OpenAI API key")
    key_input = st.text_input(
        "Paste your key",
        value=ss.api_key,
        type="password",
        placeholder="sk-...",
        help="Used only in this browser session. Never stored.",
    )
    if key_input != ss.api_key:
        ss.api_key = key_input
        # Changing the key invalidates the manager
        ss.manager = None
        ss.chat_log = []
        ss.topology_state = None

    if ss.api_key and ss.manager is None:
        try:
            ss.manager = AppManager(
                api_key=ss.api_key,
                mininet_api_url=os.environ.get(
                    "MININET_API_URL", "http://mininet-sim:8080"
                ),
            )
        except Exception as e:
            st.error(f"Could not initialise: {e}")

    st.markdown("---")
    st.subheader("📄 Documents")

    if ss.manager is None:
        st.info("Enter your API key above first.")
    else:
        uploads = st.file_uploader(
            "Upload PDFs or TXT files",
            type=["pdf", "txt"],
            accept_multiple_files=True,
        )
        if st.button("⚙️ Build index",
                     disabled=not uploads,
                     use_container_width=True):
            try:
                with st.spinner("Embedding documents..."):
                    pairs = [(f.name, f.getvalue()) for f in uploads]
                    stats = ss.manager.index_documents(pairs)
                ss.chat_log = []  # fresh chat for a fresh index
                ss.manager.memory.clear()
                st.success(
                    f"Indexed {stats['files']} file(s) → "
                    f"{stats['chunks']} chunks."
                )
                skipped = stats.get("skipped_files") or []
                if skipped:
                    skipped_text = "\n".join(f"- {item}" for item in skipped)
                    st.warning(
                        "Skipped file(s) during ingestion:\n"
                        f"{skipped_text}"
                    )
            except Exception as e:
                st.error(f"Indexing failed: {e}")

        if ss.manager.index_stats:
            st.caption(
                f"Active index: {ss.manager.index_stats['files']} file(s), "
                f"{ss.manager.index_stats['chunks']} chunks"
            )

    st.markdown("---")
    st.subheader("🧠 Memory")
    if ss.manager:
        m = ss.manager.memory_stats()
        c1, c2 = st.columns(2)
        c1.metric("Turns total", m["total_turns"])
        c2.metric("In window", m["in_window"])
        st.caption(f"Summarised turns: {m['in_summary']} "
                   f"· summary length: {m['summary_chars']} chars")
        with st.expander("Show current summary"):
            st.write(ss.manager.memory.summary or "_(none yet)_")
    else:
        st.caption("Memory will appear after setup.")

    st.markdown("---")
    st.subheader("🧪 Mininet Simulator")
    if ss.manager:
        sim_health = ss.manager.network_executor.health()
        if sim_health.get("status") == "ok":
            st.success("Simulator API reachable")
        else:
            st.warning("Simulator API unavailable")

        refresh_topo = st.button(
            "🔄 Refresh Topology State",
            use_container_width=True,
        )
        if refresh_topo or ss.topology_state is None:
            ss.topology_state = ss.manager.topology_state()
        with st.expander("Show topology state", expanded=False):
            st.json(ss.topology_state)
    else:
        st.caption("Simulator status appears after setup.")

    st.markdown("---")
    if st.button("🧹 Clear conversation", use_container_width=True):
        ss.chat_log = []
        if ss.manager:
            ss.manager.memory.clear()
        ss.topology_state = None
        st.rerun()


# ---------------------------------------------------------------------------
# Main: chat
# ---------------------------------------------------------------------------

if ss.manager is None:
    st.info("👈 Start by pasting your OpenAI API key in the sidebar.")
    st.stop()

if not ss.manager.is_ready():
    st.info("👈 Upload at least one PDF or TXT and click **Build index**.")
    st.stop()

# Render the full transcript on every rerun
for msg in ss.chat_log:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("sources"):
            st.caption(f"Sources: {msg['sources']}")

# New input
question = st.chat_input("Ask a question about your documents...")
if question:
    ss.chat_log.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            stream, docs = ss.manager.ask(question)
            # st.write_stream both displays tokens AND returns the assembled text
            answer = st.write_stream(stream)
        except Exception as e:
            answer = f"Error: {e}"
            docs = []
            st.error(answer)

        sources = ss.manager.retriever.format_sources(docs) if docs else ""
        # Don't show citations for the refusal response
        if "I don't have enough information" in answer:
            sources = ""
        if sources:
            st.caption(f"Sources: {sources}")

        if ss.manager.is_implementation_request(question):
            impl = ss.manager.implement_network_change(question)
            ss.topology_state = ss.manager.topology_state()
            if impl.get("implemented"):
                actions = ", ".join(impl.get("actions", [])) or "none"
                changes = "\n".join(f"- {c}" for c in impl.get("changes", []))
                checks = "\n".join(f"- {c}" for c in impl.get("verification", []))
                confirmation = (
                    "\n\n### Implementation Confirmation (Mininet)\n"
                    f"Applied actions: {actions}\n\n"
                    f"Changes:\n{changes}\n\n"
                    f"Verification:\n{checks}"
                )
                answer += confirmation
                st.markdown(confirmation)
            else:
                failure_note = (
                    "\n\n### Implementation Confirmation (Mininet)\n"
                    "Requested implementation was not applied.\n"
                    f"Details: {impl.get('message') or impl.get('error') or 'unknown'}"
                )
                answer += failure_note
                st.markdown(failure_note)
                st.warning(
                    f"Implementation not applied: "
                    f"{impl.get('message') or impl.get('error') or 'unknown'}"
                )

    ss.chat_log.append({
        "role": "assistant",
        "content": answer,
        "sources": sources,
    })
    # Commit to layered memory (may trigger summarisation under the hood)
    ss.manager.commit_turn(question, answer, sources)
