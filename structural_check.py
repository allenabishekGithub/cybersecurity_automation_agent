"""
Structural sanity check. Does NOT call OpenAI — just verifies the OOP
wiring: classes import, signatures match, memory's pure-Python logic
works without an LLM.
"""
import sys
import importlib


def check_imports():
    """Every component module imports without error."""
    modules = [
        "components.ingestion",
        "components.retriever",
        "components.memory",
        "components.rag_agent",
        "manager",
    ]
    for m in modules:
        try:
            importlib.import_module(m)
            print(f"  OK   import {m}")
        except Exception as e:
            print(f"  FAIL import {m}: {e}")
            return False
    return True


def check_class_surface():
    """Each class exposes the methods the UI/manager expects."""
    from components.ingestion import IngestionManager
    from components.retriever import RetrieverManager
    from components.memory import LayeredMemory, Turn
    from components.rag_agent import RAGAgent, REFUSAL
    from manager import AppManager

    expected = {
        IngestionManager: ["build_index"],
        RetrieverManager: ["retrieve", "format_context", "format_sources"],
        LayeredMemory: ["add_turn", "as_prompt_context", "clear", "stats"],
        RAGAgent: ["stream_answer"],
        AppManager: ["index_documents", "is_ready", "ask",
                     "commit_turn", "memory_stats"],
    }
    ok = True
    for cls, methods in expected.items():
        for m in methods:
            if not hasattr(cls, m):
                print(f"  FAIL {cls.__name__} missing .{m}()")
                ok = False
            else:
                print(f"  OK   {cls.__name__}.{m}")
    assert REFUSAL  # used by both prompt and UI
    return ok


def check_memory_pure_logic():
    """LayeredMemory's window/full-history logic doesn't need an LLM."""
    from components.memory import LayeredMemory

    # api_key is unused unless summarisation kicks in
    mem = LayeredMemory(api_key="fake", window_size=2, summarize_after=99)
    mem.add_turn("q1", "a1")
    mem.add_turn("q2", "a2")
    mem.add_turn("q3", "a3")

    s = mem.stats()
    assert s["total_turns"] == 3, s
    assert s["in_window"] == 2, s
    assert s["summary_chars"] == 0, s
    print(f"  OK   memory stats after 3 turns: {s}")

    ctx = mem.as_prompt_context()
    # 2 turns * 2 messages each = 4 messages; no summary system msg yet
    assert len(ctx) == 4, [type(m).__name__ for m in ctx]
    assert ctx[0].content == "q2"
    assert ctx[-1].content == "a3"
    print(f"  OK   prompt context window correct ({len(ctx)} msgs)")

    mem.clear()
    assert mem.stats()["total_turns"] == 0
    print("  OK   clear() resets history")
    return True


def main() -> int:
    print("== Imports ==")
    if not check_imports():
        return 1
    print("\n== Class surface ==")
    if not check_class_surface():
        return 1
    print("\n== Memory pure-logic ==")
    if not check_memory_pure_logic():
        return 1
    print("\nALL STRUCTURAL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
