"""Failures the pipeline treats as its own, rather than as an unexpected crash.

These sit in their own module because they are raised by one part of the pipeline and
caught by another, and both used to live in `retrieval.py` -- which is now an evaluation
arm. A pipeline exception defined inside code the pipeline no longer ships would be a
dependency in the wrong direction.

`RetrievalError` keeps its name. The spires arm raises it, `pipeline.run` catches it to
decide whether `--allow-unaugmented` applies, and the evaluation arms re-export it so
their own retrieval failures land in the same handler.
"""

from __future__ import annotations


class ContextError(RuntimeError):
    """Supplementary context could not be built. Fatal unless --allow-unaugmented."""


class RetrievalError(ContextError):
    """Retrieval produced nothing usable. Fatal unless --allow-unaugmented."""
