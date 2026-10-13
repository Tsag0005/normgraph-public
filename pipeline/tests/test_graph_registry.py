"""Two corpora must not be able to claim one node prefix.

Neo4j has no namespaces. `MERGE (n:X {key: row.key}) SET n += row` folds a second corpus
into a first the moment their prefixes match and a key collides, and nothing reports it:
the graph just contains two statutes claiming to be one. The first symptom would be a
retrieval returning provisions from an instrument nobody asked about.

So the gate has to refuse rather than warn, and it has to be checked in both directions --
that it lets the legitimate case through, and that it stops each way of colliding.

Free: no API calls, and no database unless NEO4J_URI happens to be set.

    python tests/test_graph_registry.py
"""

from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import corpus_config  # noqa: E402
import graph_registry  # noqa: E402


def test_the_configured_corpus_is_allowed():
    """Schedule 1 owns SP_ and must stay able to load."""
    problems = graph_registry.check(corpus_config.NAME, corpus_config.NODE_PREFIX)
    assert not problems, problems


def test_a_second_corpus_cannot_take_a_claimed_prefix():
    registered = graph_registry.load()
    assert registered, "nothing is registered, so this test proves nothing"
    owner, entry = next(iter(registered.items()))
    problems = graph_registry.check("some_other_corpus", entry["node_prefix"])
    assert problems, "a claimed prefix was offered to a different corpus"
    assert owner in problems[0], problems


def test_a_prefix_may_not_shadow_a_pre_prefix_label():
    """:AppNode and :RP1Entity predate prefixing and share the instance."""
    for prefix in ("APP_", "RP1_", "P_"):
        assert graph_registry.check("x", prefix), "%s was allowed" % prefix


def test_a_prefix_must_be_shaped_like_one():
    """Without the trailing underscore, a prefix cannot be told from a label that merely
    starts with the same letters -- :SPire would match a prefix of 'SP'."""
    for bad in ("spires", "SP", "sp_", "TOOLONGPREFIX_", ""):
        assert graph_registry.check("x", bad), "%r was allowed" % bad


def test_an_unclaimed_prefix_is_allowed():
    assert not graph_registry.check("part_iiic", "PI_")


def test_an_unreadable_registry_is_not_read_as_empty():
    """Falling back to "nothing is registered" on a parse error is how you merge graphs."""
    import tempfile
    original = graph_registry.REGISTRY_PATH
    handle, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with io.open(handle, "w", encoding="utf-8") as fh:
            fh.write("corpora: [this is not: valid yaml: at all\n")
        graph_registry.REGISTRY_PATH = path
        try:
            graph_registry.load()
        except graph_registry.RegistryError:
            pass
        else:
            raise AssertionError("a malformed registry was read as empty")
    finally:
        graph_registry.REGISTRY_PATH = original
        os.unlink(path)


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  PASS  %s" % name)
        except AssertionError as exc:
            failed += 1
            print("  FAIL  %s\n        %s" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  ERROR %s\n        %s: %s" % (name, type(exc).__name__, exc))
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
