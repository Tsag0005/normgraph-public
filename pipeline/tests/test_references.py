"""Cross-references: the edges the graph exists to provide.

Cross-referenced coverage is the only tier where the graph beat the no-context control,
so these edges are what the whole method rests on. Two things have to be true at once:

  the generalised parser must produce EXACTLY the edges the shipped one did on Schedule 1,
  because the recorded arm results were measured against that graph; and

  it must handle Part IIIC, where 42% of citations are relative -- "subsection (1)" --
  a form Schedule 1 uses twice and for which no mechanism existed.

Free: no API calls, no database.

    python tests/test_references.py
"""

from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _evaluation_on_path():
    """Put the repository root on sys.path so `evaluation` is importable, if it exists.

    The equivalence checks below compare this package against the superseded code it
    replaced, which lives in evaluation/. These tests run with pipeline/ as the working
    directory -- deliberately, so the suite proves the pipeline stands alone -- and that
    also means `evaluation` is not importable by default. Without this the checks would
    skip even where both trees are present, which is the silent kind of loss they exist
    to prevent.
    """
    repo = os.path.dirname(ROOT)
    for entry in (repo, os.path.join(repo, "evaluation", "edc")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return os.path.isdir(os.path.join(repo, "evaluation"))

sys.path.insert(0, ROOT)

import corpus_config  # noqa: E402
import prep_corpus  # noqa: E402
import references  # noqa: E402

PART_IIIC_PROFILE = os.path.join(ROOT, "profiles", "part_iiic.yaml")
PART_IIIC_RAW = os.path.join(ROOT, "corpora", "APPartIIIC.txt")

# The shipped parser's output on Schedule 1. Not a target to beat -- a value to reproduce.
SCHEDULE1_EDGES = 33


def _parser(profile_path=None, raw_path=None):
    profile = prep_corpus.load_profile(profile_path)
    seg = prep_corpus.segmentation(profile_path, raw_path)
    return references.ReferenceParser(profile, list(seg.provisions)), seg


def _edges(parser, seg):
    return {(pid, target)
            for pid, text in seg.provisions.items()
            for target in references.targets(parser.parse(text, pid))
            if target != pid}


# --- the equivalence that lets this replace the shipped parser ----------------------

def test_schedule1_edges_are_identical_to_the_shipped_parser():
    # The superseded hand-written parser, which lives in evaluation/. This is an
    # equivalence proof, so it can only run where both trees are present -- and the
    # pipeline is meant to run without evaluation at all. Skip rather than fail.
    try:
        _evaluation_on_path()
        from evaluation.arms.enrich_graph import parse_references
    except ImportError:
        print("  SKIP  evaluation/ not present; equivalence unchecked")
        return

    parser, seg = _parser()
    shipped = set()
    for pid, text in seg.provisions.items():
        for target in parse_references(text, corpus_config.provision_key(pid))[0]:
            bare = corpus_config.provision_key(target).replace(
                corpus_config.PROVISION_KEY_PREFIX + " ", "")
            if bare in seg.provisions and bare != pid:
                shipped.add((pid, bare))
    produced = _edges(parser, seg)
    assert len(shipped) == SCHEDULE1_EDGES, "the shipped parser changed: %d" % len(shipped)
    assert produced == shipped, "differs: %s" % sorted(produced ^ shipped)


def test_provision_key_matches_the_function_it_replaced():
    """corpus_config.provision_key dropped build_graph.normalise, whose real job is
    folding entity mentions with a synonym table. The :AppNode keys in the live graph
    were built with the old one, so the two must agree on every identifier."""
    # Equivalence against superseded code in evaluation/. Runs only where both trees
    # are present; the pipeline is meant to run without evaluation at all.
    try:
        _evaluation_on_path()
        from build_graph import normalise as normalise_mention
    except ImportError:
        print("  SKIP  evaluation/ not present; equivalence unchecked")
        return

    def old_key(pid):
        ident = pid.strip().lower()
        prefix = corpus_config.PROVISION_KEY_PREFIX
        if prefix and not ident.startswith(prefix):
            ident = "%s %s" % (prefix, ident)
        return normalise_mention(ident)[0]

    ids = prep_corpus.order()
    assert len(ids) == 62, len(ids)
    for pid in ids:
        assert old_key(pid) == corpus_config.provision_key(pid), pid
        prefixed = corpus_config.provision_key(pid)
        assert old_key(prefixed) == corpus_config.provision_key(prefixed), prefixed


# --- the template parsing that everything else depends on ---------------------------

def test_a_literal_between_components_introduces_the_next_one():
    """The bug that made every Part IIIC citation fail to resolve.

    `{section}({subsection})` must give the subsection a prefix of "(". Attaching the
    literal to the previous component instead leaves that prefix empty, so
    leading_component never splits an identifier, no section is ever recognised, and no
    relative citation can be built. Nothing errors -- the graph simply comes out empty.
    """
    components = references.parse_template("{section}({subsection})")
    assert [c.name for c in components] == ["section", "subsection"]
    assert components[1].prefix == "(", components
    assert components[1].suffix == ")", components
    assert references.leading_component("26WE(3)", components) == "26WE"


def test_single_component_templates_have_no_relative_form():
    """Schedule 1 has 2 relative citations and Part IIIC has 56. That is not a coincidence.

    A relative citation is an identifier with its leading component left off, so it can
    only exist where identifiers are composed. `{clause}` has nothing to leave off.
    """
    components = references.parse_template("{clause}")
    assert references.relative_forms(["6.1", "6.2", "12.10"], components) == []
    parser, _seg = _parser()
    assert parser.relative is None


def test_relative_forms_come_from_the_identifiers_that_exist():
    components = references.parse_template("{section}({subsection})")
    tails = references.relative_forms(["26WE(1)", "26WE(2)", "26WB"], components)
    assert set(tails) == {"(1)", "(2)"}, tails
    assert tails.index("(1)") >= 0


# --- Part IIIC: the forms Schedule 1 never exercised --------------------------------

def test_relative_citations_resolve_against_the_containing_provision():
    parser, seg = _parser(PART_IIIC_PROFILE, PART_IIIC_RAW)
    kinds = {c.raw: c for c in parser.parse(seg.provisions["26WL(3)"], "26WL(3)")}
    assert "(2)" in kinds, kinds
    assert kinds["(2)"].kind == "internal"
    assert kinds["(2)"].target == "26WL(2)", kinds["(2)"]


def test_a_whole_section_citation_reaches_its_subsections():
    """"has effect subject to section 26WF" means all of 26WF, which is five provisions."""
    parser, seg = _parser(PART_IIIC_PROFILE, PART_IIIC_RAW)
    targets = references.targets(parser.parse(seg.provisions["26WE(3)"], "26WE(3)"))
    assert "26WF(1)" in targets and "26WF(5)" in targets, targets
    assert "26WE(2)" in targets, "the relative citation in the same sentence was missed"


def test_paragraph_level_relatives_are_not_edges():
    """"paragraph (a)" points inside the provision making it, so it carries no edge."""
    parser, seg = _parser(PART_IIIC_PROFILE, PART_IIIC_RAW)
    for pid in ("26WE(2)", "26WG"):
        for citation in parser.parse(seg.provisions[pid], pid):
            assert citation.target != pid or citation.kind == "self", citation


def test_part_iiic_resolution_rate_is_healthy():
    """The validation gate. A broken pattern shows up here as a collapsed rate."""
    parser, seg = _parser(PART_IIIC_PROFILE, PART_IIIC_RAW)
    per = {pid: parser.parse(t, pid) for pid, t in seg.provisions.items()}
    summary = references.summarise(per)
    assert summary["internal"] > 100, summary
    assert summary["internal_resolution_rate"] > 0.9, summary
    assert len(_edges(parser, seg)) > 100, "far fewer edges than measured"


# --- externals ----------------------------------------------------------------------

def test_an_instrument_does_not_cite_itself_by_name():
    """Schedule 1 has a level it calls a `principle`, so "Australian Privacy Principle 6"
    is internal to it. Treating that as external manufactures a stub for something the
    graph already contains. Part IIIC has no such level, so there the same phrase is a
    genuine reference out."""
    schedule1, _ = _parser()
    assert schedule1._names_a_level("Australian Privacy Principle")
    part_iiic, _ = _parser(PART_IIIC_PROFILE, PART_IIIC_RAW)
    assert not part_iiic._names_a_level("Australian Privacy Principle")


def test_singular_and_plural_are_one_stub():
    assert references._singular("Principles") == "Principle"
    assert references._singular("Act") == "Act"
    assert references._singular("Access") == "Access"


def test_external_detection_does_not_reach_across_a_sentence():
    """An earlier version looked 60 characters ahead and read "as soon as practicable
    after the commencement" as an external reference."""
    parser, seg = _parser(PART_IIIC_PROFILE, PART_IIIC_RAW)
    citations = parser.parse(seg.provisions["26WL(3)"], "26WL(3)")
    assert not [c for c in citations if c.kind == "external"], citations


def test_range_expansion_is_bounded_by_the_corpus():
    """`enrich_graph._expand` capped ranges at 13, the number of Australian Privacy
    Principles. The bound here is which identifiers actually exist."""
    parser, _seg = _parser()
    expanded = parser._expand("5.1 to 5.2")
    assert all(e in parser.known for e in expanded), expanded


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
