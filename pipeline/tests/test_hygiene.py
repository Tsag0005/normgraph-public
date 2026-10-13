"""The .txt has to be a faithful rendering of the statute, and only this checks it.

Every other gate in the build looks at STRUCTURE. None of them can see a file whose
structure is intact and whose text is mangled -- which is exactly what a PDF text layer
produces. Measured by damaging Schedule 1 three ways and checking whether the provision
text survived:

    damage                        provisions found   texts corrupted
    hard wrap, no hyphenation     62 (correct)        0 / 62
    hard wrap with hyphenation    62 (correct)       45 / 62
    running headers + page nums   62 (correct)       13 / 62

All three segment to exactly 62 provisions with an interior orphan rate under the
ceiling, so provision count, orphan rate and reference resolution all pass. You would get
a graph and test cases derived from corrupted statutory text, with nothing reporting a
problem.

The damage is reconstructed here rather than committed, so the fixtures cannot drift from
the corpus they are derived from. Deterministic: the hyphenation uses a seeded Random.

Free: no API calls, no database.

    python tests/test_hygiene.py
"""

from __future__ import annotations

import io
import os
import random
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import prep_corpus  # noqa: E402

SCHEDULE = os.path.join(ROOT, "corpora", "APPschedule1.txt")
PART_IIIC = os.path.join(ROOT, "corpora", "APPartIIIC.txt")


def _lines(path):
    return io.open(path, encoding="utf-8").read().splitlines()


def _plain_wrap(lines, width=58):
    """A text layer that wraps but does not hyphenate. Survivable: segmentation rejoins
    unmatched lines and normalise() collapses the whitespace."""
    out = []
    for line in lines:
        out.extend(textwrap.wrap(line, width) or [""])
    return out


def _hyphenated_wrap(lines, width=58, seed=0):
    """What a real PDF text layer gives you: justification splits words across the break."""
    rng = random.Random(seed)
    out = []
    for line in lines:
        for chunk in (textwrap.wrap(line, width) or [""]):
            words = chunk.split()
            if words and len(words[-1]) > 6 and rng.random() < 0.35:
                cut = len(words[-1]) // 2
                tail = words[-1][cut:]
                words[-1] = words[-1][:cut] + "-"
                out.append(" ".join(words))
                out.append(tail)
            else:
                out.append(chunk)
    return out


def _paginated(lines, pitch=40):
    out, page = [], 1
    for index, line in enumerate(lines):
        if index and index % pitch == 0:
            out += ["", "Privacy Act 1988", "%d" % page, ""]
            page += 1
        out.append(line)
    return out


# --- the files as held must pass ----------------------------------------------------

def test_both_real_corpora_pass():
    for path in (SCHEDULE, PART_IIIC):
        report = prep_corpus.hygiene(_lines(path))
        assert report.ok, "%s: %s" % (os.path.basename(path), report.problems)


# --- damage that corrupts text must stop the build ----------------------------------

def test_a_hyphenated_wrap_is_stopped():
    report = prep_corpus.hygiene(_hyphenated_wrap(_lines(SCHEDULE)))
    assert not report.ok
    assert report.dangling_hyphens > 50, report.dangling_hyphens
    assert any("hyphen" in p for p in report.problems), report.problems


def test_pagination_is_stopped():
    report = prep_corpus.hygiene(_paginated(_lines(SCHEDULE)))
    assert not report.ok
    assert report.numeric_only_lines > 10, report.numeric_only_lines
    assert any("running header" in p for p in report.problems), report.problems
    assert any("page number" in p for p in report.problems), report.problems


# --- damage that corrupts nothing must NOT stop the build ---------------------------

def test_a_plain_wrap_is_allowed_because_it_corrupts_nothing():
    """The check that keeps the gate honest.

    A wrap with no hyphenation trips the line-length warning, and the provision texts
    come through byte-identical -- segmentation appends unmatched lines to the open
    provision. Rejecting it would be rejecting a file that works.
    """
    wrapped = _plain_wrap(_lines(SCHEDULE))
    report = prep_corpus.hygiene(wrapped)
    assert report.ok, report.problems
    assert report.warnings, "a wrap should still be flagged as a warning"

    profile = prep_corpus.load_profile()
    clean = prep_corpus.segment(_lines(SCHEDULE), profile).provisions
    damaged = prep_corpus.segment(wrapped, profile).provisions
    assert clean == damaged, "the premise is wrong: a plain wrap DOES corrupt text"


def test_wrap_fragments_are_not_mistaken_for_running_headers():
    """Repetition alone is not the signal, and this is why.

    Wrapping leaves fragments -- "personal information" -- repeating five or more times.
    A running header sits at the page pitch instead: measured coefficient of variation
    0.02 against 0.73-0.93 for the fragments.
    """
    wrapped = _plain_wrap(_lines(SCHEDULE))
    assert not prep_corpus._running_headers(
        [prep_corpus.normalise(l) for l in wrapped if l.strip()])
    paginated = _paginated(_lines(SCHEDULE))
    headers = prep_corpus._running_headers(
        [prep_corpus.normalise(l) for l in paginated if l.strip()])
    assert headers and "Privacy Act 1988" in headers[0], headers


# --- the claim the gate rests on ----------------------------------------------------

def test_the_structural_gates_cannot_see_this_damage():
    """If they could, this gate would be redundant. They cannot.

    Both corrupting variants segment to exactly the right provision count with an orphan
    rate well under the ceiling, and their text is wrong.
    """
    import build_corpus

    profile = prep_corpus.load_profile()
    clean = prep_corpus.segment(_lines(SCHEDULE), profile).provisions
    for label, damaged_lines, expect_corrupt in (
            ("hyphenated", _hyphenated_wrap(_lines(SCHEDULE)), 30),
            ("paginated", _paginated(_lines(SCHEDULE)), 5)):
        seg = prep_corpus.segment(damaged_lines, profile)
        assert len(seg.provisions) == len(clean), \
            "%s: provision count changed, so the count gate WOULD catch it" % label
        rate = seg.interior_orphans / float(seg.non_blank or 1)
        assert rate <= build_corpus.MAX_INTERIOR_ORPHAN_RATE, \
            "%s: orphan rate %.3f would trip the gate" % (label, rate)
        corrupted = sum(1 for k in clean if clean[k] != seg.provisions.get(k))
        assert corrupted >= expect_corrupt, \
            "%s: only %d texts corrupted, expected at least %d" % (label, corrupted, expect_corrupt)


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
