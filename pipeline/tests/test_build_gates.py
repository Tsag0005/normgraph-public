"""The gates must stop a broken corpus before it costs anything.

Each stage of the build can fail without raising and without producing an obviously empty
result -- it just produces a graph that is quietly wrong. Every failure the gates check
for has happened once in this project already:

    a profile that segments the file into almost nothing
    a citation parser that resolves nothing, because a template literal was misplaced
    an extraction whose modality is NONE for every norm

A gate that only fires on the cases you thought of is worth little, so this checks the
other direction too: the real corpus must pass all of them.

One case is worth its own note. A profile whose identifier pattern recognises nothing
produces a resolution rate of 100% -- zero resolved out of zero internal-looking -- which
looks perfect. It is caught only by the second reference gate, which asks how many
provisions cite anything at all. Neither check alone is sufficient.

Free: no API calls, no database.

    python tests/test_build_gates.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import build_corpus  # noqa: E402
import corpus_config  # noqa: E402
import prep_corpus  # noqa: E402

SCHEDULE = os.path.join(ROOT, "corpora", "APPschedule1.txt")
PROFILE = os.path.join(ROOT, "profiles", "app_schedule1.yaml")


def _mutated(mutate):
    """A copy of the Schedule 1 profile with one thing broken. Returns its path."""
    import yaml
    with io.open(PROFILE, encoding="utf-8") as fh:
        profile = yaml.safe_load(fh)
    mutate(profile)
    handle, path = tempfile.mkstemp(suffix=".yaml")
    with io.open(handle, "w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(profile, fh)
    prep_corpus._CACHE.clear()
    return path


def _expect_stop(mutate, label):
    path = _mutated(mutate)
    try:
        build_corpus.gate_segmentation(path, SCHEDULE)
        build_corpus.gate_references(path, SCHEDULE)
    except (build_corpus.GateFailed, SystemExit):
        return
    finally:
        prep_corpus._CACHE.clear()
        os.unlink(path)
    raise AssertionError("%s was not caught" % label)


def test_the_real_corpus_passes_every_gate():
    """A gate that stops everything is not a gate."""
    prep_corpus._CACHE.clear()
    segmentation = build_corpus.gate_segmentation(PROFILE, SCHEDULE)
    assert segmentation["provisions"] == 62, segmentation
    summary = build_corpus.gate_references(PROFILE, SCHEDULE)
    assert summary["internal"] == 33, summary
    build_corpus.gate_prefix()


def test_a_provision_pattern_matching_nothing_is_caught():
    def mutate(profile):
        for level in profile["levels"]:
            if level["role"] == "provision":
                level["pattern"] = r"^ZZZ(\d+)$"
    _expect_stop(mutate, "a provision pattern matching nothing")


def test_an_identifier_pattern_recovering_nothing_is_caught():
    """The subtle one: 0 of 0 resolved reads as 100%, so the rate alone misses it."""
    def mutate(profile):
        profile["identifier_pattern"] = r"\b(ZZZ\d+)\b"
    _expect_stop(mutate, "an identifier pattern recovering nothing")


def test_a_provision_pattern_matching_every_line_is_caught():
    def mutate(profile):
        for level in profile["levels"]:
            if level["role"] == "provision":
                level["pattern"] = r"^(.*)$"
    _expect_stop(mutate, "a provision pattern matching every line")


def test_the_real_corpus_has_multiple_modalities():
    assert build_corpus.MAX_SINGLE_MODALITY_SHARE < 1.0
    counts = build_corpus.gate_modalities()
    assert len(counts) > 1, "the real corpus should use several modalities: %s" % counts
    top = max(counts.values()) / float(sum(counts.values()))
    assert top <= build_corpus.MAX_SINGLE_MODALITY_SHARE, counts


def test_modality_gate_allows_a_corpus_of_obligations():
    """A short document containing only 'must' rules still has valid modalities."""
    import yaml
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "example.clean.yaml")
        with io.open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump({"norms": [{"deontic_modality": "OBLIGATION"}] * 3}, fh)
        with patch.object(build_corpus.corpus_config, "provision_glob",
                          return_value=os.path.join(directory, "*.clean.yaml")):
            assert build_corpus.gate_modalities() == {"OBLIGATION": 3}


def test_modality_gate_stops_unclassified_norms():
    """Uniform NONE or missing modalities still indicate a failed extraction."""
    import yaml
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "example.clean.yaml")
        with patch.object(build_corpus.corpus_config, "provision_glob",
                          return_value=os.path.join(directory, "*.clean.yaml")):
            for norms in ([{"deontic_modality": "NONE"}], [{}]):
                with io.open(path, "w", encoding="utf-8") as fh:
                    yaml.safe_dump({"norms": norms}, fh)
                try:
                    build_corpus.gate_modalities()
                except build_corpus.GateFailed:
                    pass
                else:
                    raise AssertionError("unclassified norms passed the modality gate")


def test_bootstrap_finds_a_config_by_what_it_points_at():
    """corpus.part_iiic.yaml and APPartIIIC.txt belong together; no filename rule pairs
    them. Matching on `paths.raw` is what stops a second config being induced for a
    corpus that already has one."""
    found = build_corpus.existing_config_for(os.path.join(ROOT, "corpora", "APPartIIIC.txt"))
    assert found and os.path.basename(found) == "corpus.part_iiic.yaml", found
    assert build_corpus.existing_config_for(os.path.join(ROOT, "nosuchfile.txt")) is None


def test_bootstrap_leaves_the_configured_corpus_alone():
    """The common case must not re-execute: it is already the corpus being asked for."""
    class Args:
        dry_run = True
        model = "gpt-4o"
    assert build_corpus.bootstrap(corpus_config.RAW_PATH, Args()) is None


def test_the_corpus_slug_is_filename_safe():
    assert build_corpus.corpus_slug("APPartIIIC.txt") == "appartiiic"
    assert build_corpus.corpus_slug("/a/b/My Statute (2024).TXT") == "my_statute_2024"


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
