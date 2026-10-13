"""Token counting, used to hold context blocks inside their configured budget.

Extracted from `serialisation.py` when that module became an evaluation arm. The spires
arm needs to know how large a context block is before it emits one, and that is not a
fact about triple serialisation.
"""

from __future__ import annotations


def estimate_tokens(text: str, model: str):
    """Token count via tiktoken when available, else a chars/4 heuristic.

    Returns the count and how it was arrived at, so a run record says which. The
    heuristic is wrong enough to matter -- it under-counts legislative text, which is
    dense in long words -- and a budget check silently using it would be misleading.
    """
    try:
        import tiktoken
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text)), "tiktoken"
    except ImportError:
        return max(1, len(text) // 4), "heuristic(chars/4)"
