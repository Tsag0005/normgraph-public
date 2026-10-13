"""The APPLICABILITY rule must speak the instrument's own vocabulary.

`clean_extraction` relabels "This <unit> applies in relation to X if ..." provisions as
APPLICABILITY. They command nothing themselves -- they supply the conditions under which
an exception named by another provision is met -- and the extractor consistently calls
them NONE, which buries them among genuinely inert clauses.

The rule was written as the literal alternation `(subclause|principle)`, which is
Schedule 1's vocabulary and nothing else's. Part IIIC says "This section applies if",
so on that corpus the rule matched nothing at all: five provisions would have kept a
NONE label, with no error and no warning, and the failure would only have surfaced as a
graph that mysteriously contained no APPLICABILITY norms.

Two things are checked, and the second matters as much as the first: the rule now catches
Part IIIC, **and** it still behaves identically on Schedule 1. Widening a pattern is a
cheap way to introduce false positives into a corpus whose results are already recorded.

Free: no API calls, no database.

    python tests/test_modality_vocabulary.py
"""

from __future__ import annotations

import importlib.util
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import prep_corpus  # noqa: E402

# The pattern exactly as it was before it became corpus-driven.
LITERAL = re.compile(r"^\s*this\s+(subclause|principle)\s+applies\b", re.I)

# The clause the rule keys on, wherever it appears in a provision.
SHAPE = re.compile(r"(This \w+ applies[^.:;]*)", re.I)

# Part IIIC's scope subsections. These are not incidental provisions: they open the
# eligible-data-breach definition, the assessment duty, the statement requirement, the
# notification duty and the Commissioner's information power.
PART_IIIC_EXPECTED = ["26WE(1)", "26WH(1)", "26WK(1)", "26WL(1)", "26WU(1)"]


def _cleaner():
    """Import spires/clean_extraction.py by path -- spires/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        "clean_extraction", os.path.join(ROOT, "spires", "clean_extraction.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _profile(name):
    import yaml
    path = os.path.join(ROOT, "profiles", name)
    with io.open(path, encoding="utf-8") as fh:
        return prep_corpus.Profile.from_dict(yaml.safe_load(fh))


def _sentences(profile_name, raw_name):
    seg = prep_corpus.segmentation(os.path.join(ROOT, "profiles", profile_name),
                                   os.path.join(ROOT, "corpora", raw_name))
    return [(pid, m.group(1))
            for pid, text in seg.provisions.items()
            for m in SHAPE.finditer(text)]


def test_part_iiic_applicability_is_now_recognised():
    pattern = _cleaner().applicability_pattern(_profile("part_iiic.yaml"))
    found = _sentences("part_iiic.yaml", "APPartIIIC.txt")
    missed_before = [pid for pid, s in found if not LITERAL.match(s)]
    caught_now = [pid for pid, s in found if pattern.match(s)]
    assert missed_before == PART_IIIC_EXPECTED, \
        "expected the literal pattern to miss %s, it missed %s" % (PART_IIIC_EXPECTED, missed_before)
    assert caught_now == PART_IIIC_EXPECTED, \
        "expected %s to be caught, got %s" % (PART_IIIC_EXPECTED, caught_now)


def test_schedule1_behaviour_is_unchanged():
    """Widening a pattern is a cheap way to invent false positives on recorded results."""
    pattern = _cleaner().applicability_pattern(_profile("app_schedule1.yaml"))
    found = _sentences("app_schedule1.yaml", "APPschedule1.txt")
    assert found, "no sentences of this shape found in Schedule 1 -- the probe is broken"
    before = [bool(LITERAL.match(s)) for _pid, s in found]
    after = [bool(pattern.match(s)) for _pid, s in found]
    assert before == after, "the widened pattern changed Schedule 1: %s vs %s" % (before, after)


def test_units_come_from_both_level_names_and_citation_nouns():
    """Neither source alone is enough, which is why they are unioned.

    "principle" and "clause" are what Schedule 1 calls its levels; "subclause" only ever
    appears there as a citation. Taking one and not the other loses half the vocabulary.
    """
    cleaner = _cleaner()
    units = set(cleaner._structural_units(_profile("app_schedule1.yaml")))
    assert "principle" in units, "level names are not contributing"
    assert "subclause" in units, "citation nouns are not contributing"
    assert "section" in set(cleaner._structural_units(_profile("part_iiic.yaml")))


def test_longer_units_are_tried_first():
    """An alternation that puts `clause` first matches inside `subclause`."""
    units = _cleaner()._structural_units(_profile("app_schedule1.yaml"))
    assert units.index("subclause") < units.index("clause"), units


def test_a_bearer_that_names_provisions_demotes_applicability():
    """The rule that replaced a per-clause override.

    Neutralising the schema's worked examples made the extractor label APP 4.4
    APPLICABILITY -- three times, including after the abstract description was sharpened
    and after it was given a synthetic contrast case closely matching 4.4's own wording.
    The extraction is not wrong about the structure, only about the label: it reports the
    bearer as "Australian Privacy Principles 5 to 13", which is the answer. A provision
    whose "applies" binds OTHER provisions turns rules on; it does not state when its own
    exception is met.
    """
    cleaner = _cleaner()
    cleaner.MANUAL_OVERRIDES = {}
    norm = {"deontic_modality": "APPLICABILITY",
            "bearer": "AUTO:Australian%20Privacy%20Principles%205%20to%2013",
            "action": "AUTO:apply"}
    assert cleaner.bearer_names_provisions(norm)
    assert cleaner.relabel_modality(norm, None, "4.4", 2) == "NONE"


def test_the_demotion_leaves_a_real_applicability_alone():
    """6.2 binds an APP entity, not a set of provisions, and must stay APPLICABILITY."""
    cleaner = _cleaner()
    cleaner.MANUAL_OVERRIDES = {}
    norm = {"deontic_modality": "APPLICABILITY", "bearer": "AUTO:APP%20entity"}
    assert not cleaner.bearer_names_provisions(norm)
    assert cleaner.relabel_modality(norm, None, "6.2", 1) == "APPLICABILITY"


def test_the_demotion_needs_a_number_as_well_as_a_structural_word():
    """"the entity described in paragraph (a)" has the word and is not a set of
    provisions. Requiring a digit is what keeps the rule off it."""
    cleaner = _cleaner()
    norm = {"deontic_modality": "APPLICABILITY",
            "bearer": "AUTO:the%20entity%20described%20in%20paragraph%20(a)"}
    assert not cleaner.bearer_names_provisions(norm)


def test_the_demotion_fires_on_no_existing_applicability_norm():
    """Checked against the whole corpus, not just the case it was written for.

    All 21 APPLICABILITY norms in Schedule 1 must be untouched, or the rule would be
    rewriting results that were already measured.
    """
    import glob
    import yaml
    cleaner = _cleaner()
    fired = []
    for path in glob.glob(os.path.join(ROOT, "spires", "output", "app_*.clean.yaml")):
        document = yaml.safe_load(io.open(path, encoding="utf-8")) or {}
        for norm in (document.get("norms") or []):
            if (norm or {}).get("deontic_modality") == "APPLICABILITY":
                if cleaner.bearer_names_provisions(norm):
                    fired.append(document.get("provision_id"))
    assert not fired, "the demotion would rewrite existing norms in: %s" % fired


def test_falls_back_when_no_profile_is_available():
    """A cleaning run must not die because a profile is missing or malformed."""
    cleaner = _cleaner()
    broken = prep_corpus.Profile(name="x", description="", levels=[], id_template="",
                                 citation_nouns=[])
    units = cleaner._structural_units(broken)
    assert "subclause" in units and "principle" in units, units


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
