"""Known-answer controls for the structure inducer.

A validator is only worth having if it accepts right answers and rejects wrong ones, and
neither half can be taken on faith. The same argument as `score_soundness --self-test`:
an earlier version of that judge rejected every scenario, which looked like a strict
judge until someone checked whether anything could pass.

Positive control: the hand-written Schedule 1 profile must reproduce `prep_app.py`
exactly -- the same 62 provision identifiers, not merely the same count.

Negative controls: nine mutations, each breaking the profile in a way a model plausibly
would, each of which must be caught. A mutation that slips through is a hole.

Everything here is free. No API calls, no network, no graph.

    python tests/test_induce_structure.py
    pytest tests/test_induce_structure.py
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

import induce_structure as I  # noqa: E402

SCHEDULE = os.path.join(ROOT, "corpora", "APPschedule1.txt")
PART_IIIC = os.path.join(ROOT, "corpora", "APPartIIIC.txt")
PROFILE_SCHEDULE = os.path.join(ROOT, "profiles", "app_schedule1.yaml")
PROFILE_PART_IIIC = os.path.join(ROOT, "profiles", "part_iiic.yaml")

# What prep_app.py / enrich_graph.raw_clause_text() produce. The graph in Aura is keyed
# on these, so reproducing the number without reproducing the ids would be worthless.
REFERENCE_PROVISIONS = 62
# 95 subsections plus the 11 sections that have none. Audited against the text: of the 28
# sections, the 17 that were dropped as standalone provisions all have subsections that
# became provisions in their own right, and none of the 11 kept has any. Nothing is lost
# either way, which is the property that matters.
PART_IIIC_PROVISIONS = 106


def _lines(path):
    return io.open(path, encoding="utf-8").read().splitlines()


def _profile(path):
    import yaml
    with io.open(path, encoding="utf-8") as fh:
        return I.Profile.from_dict(yaml.safe_load(fh))


# --- positive controls -------------------------------------------------------------

def test_schedule1_profile_is_accepted():
    val = I.validate(_lines(SCHEDULE), _profile(PROFILE_SCHEDULE))
    assert val.ok, "the known-good profile was rejected: %s" % val.problems
    assert val.metrics["provisions"] == REFERENCE_PROVISIONS


def test_schedule1_ids_match_the_handwritten_parser():
    """The strong form: same identifiers, not just the same count."""
    # The superseded hand-written parser, which lives in evaluation/. This is an
    # equivalence proof, so it can only run where both trees are present -- and the
    # pipeline is meant to run without evaluation at all. Skip rather than fail.
    try:
        _evaluation_on_path()
        from evaluation.arms import enrich_graph
    except ImportError:
        print("  SKIP  evaluation/ not present; equivalence unchecked")
        return
    reference = {k.replace("subclause ", "") for k in enrich_graph.raw_clause_text()}
    profile = _profile(PROFILE_SCHEDULE)
    I.compile_profile(profile)
    got = set(I.segment(_lines(SCHEDULE), profile).provisions)
    assert got == reference, "differs: %s" % sorted(got ^ reference)


def test_prep_corpus_matches_the_parser_it_replaces():
    """The claim Stage 1 rests on: byte-identical text, not merely the same count.

    `spires/extract_corpus.py` feeds this text to the extractor one provision at a time.
    If a single character differs, the SPIRES inputs differ, and the graph built from a
    profile is not the graph the recorded results came from.
    """
    import prep_corpus
    # Equivalence against superseded code in evaluation/. Runs only where both trees
    # are present; the pipeline is meant to run without evaluation at all.
    try:
        _evaluation_on_path()
        from evaluation.arms import enrich_graph
    except ImportError:
        print("  SKIP  evaluation/ not present; equivalence unchecked")
        return

    reference = {k.replace("subclause ", ""): v
                 for k, v in enrich_graph.raw_clause_text().items()}
    produced = prep_corpus.provisions()
    assert set(produced) == set(reference),         "identifiers differ: %s" % sorted(set(produced) ^ set(reference))
    differing = [k for k in reference if reference[k] != produced[k]]
    assert not differing, "%d provision text(s) differ, e.g. %s" % (
        len(differing), differing[:3])


def test_prep_corpus_order_is_document_order():
    """The sort key. Deriving one from the identifier grammar is what crashed on 26WE(1)."""
    import prep_corpus
    order = prep_corpus.order()
    assert order[:3] == ["1.1", "1.2", "1.3"], order[:3]
    assert order[-1] == "13.5", order[-1]
    # Schedule 1 has exactly one identifier where the two orders disagree, and it is
    # enough: sorted() puts 12.10 before 12.2, the Schedule puts it after 12.9.
    assert order != sorted(order), "the order is lexicographic, not document order"
    assert order.index("12.10") > order.index("12.9"),         "12.10 sorted before 12.9 -- lexicographic ordering has crept back in"


def test_part_iiic_profile_is_accepted():
    """A composed identifier scheme, which Schedule 1 never exercises."""
    val = I.validate(_lines(PART_IIIC), _profile(PROFILE_PART_IIIC))
    assert val.ok, val.problems
    assert val.metrics["provisions"] == PART_IIIC_PROVISIONS


def test_no_section_is_lost_when_deciding_if_it_stands_alone():
    """A section is either a provision itself or has subsections that are. Never neither.

    This is the property the `min_provision_words` threshold has to preserve, and it is
    worth asserting directly: the provision count alone would not notice a section
    quietly falling through the gap between the two cases.
    """
    import re
    profile = _profile(PROFILE_PART_IIIC)
    I.compile_profile(profile)
    seg = I.segment(_lines(PART_IIIC), profile)
    section_re = re.compile(r"^(\d{2}[A-Z]{1,3}) [A-Z].+$")
    sections = {m.group(1) for m in
                (section_re.match(I.normalise(l)) for l in _lines(PART_IIIC)) if m}
    standalone = {k for k in seg.order if "(" not in k}
    with_subsections = {k.split("(")[0] for k in seg.order if "(" in k}
    lost = sections - standalone - with_subsections
    assert not lost, "sections represented nowhere in the output: %s" % sorted(lost)


def test_container_sections_become_provisions():
    """26WB is a heading and one sentence; 26WH is a heading, a sub-heading and then (1).

    The first must stand as a provision in its own right, the second must not -- its
    `Scope` line is a sub-heading, not a rule.
    """
    profile = _profile(PROFILE_PART_IIIC)
    I.compile_profile(profile)
    seg = I.segment(_lines(PART_IIIC), profile)
    assert "26WB" in seg.provisions, "a section with no subsections was dropped"
    assert "26WH" not in seg.provisions, "a bare sub-heading was kept as a provision"
    assert "26WH(1)" in seg.provisions and "26WH(2)" in seg.provisions


def test_no_stale_capture_leaks_into_a_later_identifier():
    """26WD follows 26WC(2). If the subsection capture survives, it becomes 26WD(2)."""
    profile = _profile(PROFILE_PART_IIIC)
    I.compile_profile(profile)
    seg = I.segment(_lines(PART_IIIC), profile)
    assert "26WD" in seg.provisions
    assert "26WD(2)" not in seg.provisions


# --- negative controls -------------------------------------------------------------

def _mutate(path, fn):
    profile = _profile(path)
    fn(profile)
    return profile


def _set_pattern(profile, level_name, pattern):
    for lv in profile.levels:
        if lv.name == level_name:
            lv.pattern = pattern
            return
    raise AssertionError("no level %r" % level_name)


MUTATIONS = [
    ("provision pattern matches everything",
     lambda p: _set_pattern(p, "clause", "^.*$")),
    ("provision pattern matches nothing",
     lambda p: _set_pattern(p, "clause", r"^ZZZ(\d+)$")),
    ("provision pattern is not anchored",
     lambda p: _set_pattern(p, "clause", r"(\d+\.\d+) (.+)$")),
    ("provision pattern does not compile",
     lambda p: _set_pattern(p, "clause", r"^(\d+\.\d+(.+)$")),
    ("provision pattern can backtrack catastrophically",
     lambda p: _set_pattern(p, "clause", r"^(\w+\s*)+\.(\d+)$")),
    ("no level is marked as the provision",
     lambda p: [setattr(lv, "role", "body") for lv in p.levels]),
    ("an unknown role is used",
     lambda p: _set_pattern(p, "clause", r"^(\d+\.\d+) (.+)$") or
               [setattr(lv, "role", "clause") for lv in p.levels if lv.name == "clause"]),
    ("provision pattern only matches the first principle",
     lambda p: _set_pattern(p, "clause", r"^(1\.\d+) (.+)$")),
    ("two levels claim the same lines",
     lambda p: _set_pattern(p, "note", r"^\(([a-z]{1,4})\) (.*)$")),
    ("the id template drops the part that makes ids unique",
     lambda p: setattr(p, "id_template", "constant")),
]


def test_every_mutation_is_rejected():
    lines = _lines(SCHEDULE)
    survived = []
    for label, fn in MUTATIONS:
        val = I.validate(lines, _mutate(PROFILE_SCHEDULE, fn))
        if val.ok:
            survived.append(label)
    assert not survived, "these broken profiles were accepted: %s" % survived


def test_missed_heading_form_is_surfaced():
    """The induced Part IIIC profile misses "Subdivision A - ...", and must say so.

    It is not a rejection -- the identifiers are right and no text is lost, it is merely
    attached to the provision above. But it is the difference between the model's profile
    and the hand-written one, and it has to be visible or nobody will correct it.
    """
    induced = os.path.join(ROOT, "profiles", "part_iiic.induced.yaml")
    if not os.path.exists(induced):
        return                                  # induction has not been run in this checkout
    val = I.validate(_lines(PART_IIIC), _profile(induced))
    assert val.ok, val.problems
    assert any("Subdivision" in w for w in val.warnings),         "the missed Subdivision heading was not reported: %s" % val.warnings


def test_induced_profile_agrees_with_the_handwritten_one():
    """Same identifiers from a profile a model wrote and one a reader wrote."""
    induced = os.path.join(ROOT, "profiles", "part_iiic.induced.yaml")
    if not os.path.exists(induced):
        return
    out = []
    for path in (PROFILE_PART_IIIC, induced):
        profile = _profile(path)
        I.compile_profile(profile)
        out.append(set(I.segment(_lines(PART_IIIC), profile).provisions))
    assert out[0] == out[1], "differ: %s" % sorted(out[0] ^ out[1])


# Input hygiene used to be tested here with synthetic fixtures. It moved to
# tests/test_hygiene.py, which derives its damage from the real corpora and can
# therefore assert the thing that matters: that the damage corrupts provision
# text while every structural gate still passes.


# --- runner ------------------------------------------------------------------------

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
