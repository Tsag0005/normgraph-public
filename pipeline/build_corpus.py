"""One command from a .txt file to a knowledge graph, with a gate between each stage.

    python build_corpus.py APPartIIIC.txt --dry-run   # what it would do, and what it costs
    python build_corpus.py APPartIIIC.txt             # do it

The gates are the point. Every stage here can fail in a way that produces no error and no
empty result -- just a graph that is quietly wrong -- and each of those failures has
already happened once in this project:

    a structural profile that segments the file into three provisions
    a citation parser that resolves nothing, because a template literal was misplaced
    an extraction whose modality is NONE for every norm
    a second corpus loaded under a prefix the first one owns, merging both graphs

So each stage reports a number, that number is checked against a threshold, and the run
stops rather than proceeding to spend money on a corpus that was already broken. The
thresholds are deliberately loose. They are a smoke alarm for "this did not work", not a
quality score.

Only two stages cost anything: extraction, which is one LLM call per provision, and
verification, which is one per norm with an exception. Everything else is local, and
--dry-run stops before either.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus_config  # noqa: E402
import graph_registry  # noqa: E402
import prep_corpus  # noqa: E402
import references  # noqa: E402

# Every stage below is a subprocess, and each one reads the LLM key from the environment
# it inherits from here. .env is the documented portable way to set it, but it was only
# ever read by the Neo4j loader and the generator -- not by the build path. So a checkout
# configured entirely through .env authenticated fine for the graph load and failed for
# every extraction, which is the expensive half.
try:
    from dotenv import load_dotenv
    load_dotenv()                      # does not overwrite anything already in the env
except ImportError:
    pass

PYTHON = sys.executable
ROOT = os.path.dirname(os.path.abspath(__file__))

# Loose on purpose: a smoke alarm, not a quality score. Each corresponds to a failure
# that has actually occurred, and each would otherwise surface three stages later.
MIN_PROVISIONS = 5
MAX_INTERIOR_ORPHAN_RATE = 0.10
MIN_REFERENCE_RESOLUTION = 0.50
MIN_PROVISIONS_WITH_EDGES = 0.05     # a corpus where nothing cites anything is suspect
MAX_SINGLE_MODALITY_SHARE = 0.95     # every norm NONE means the schema did not land


class GateFailed(RuntimeError):
    """A stage produced a number that says it did not work. Always stops the run."""


def _say(stage: str, message: str) -> None:
    print("[%s] %s" % (stage, message))


def _run(stage: str, argv: list) -> None:
    _say(stage, "$ " + " ".join(argv[1:]))
    result = subprocess.run(argv, cwd=ROOT)
    if result.returncode != 0:
        raise GateFailed("%s exited %d" % (stage, result.returncode))


# --- stages -------------------------------------------------------------------------

def stage_profile(corpus_path: str, profile_path: str, model: str, dry_run: bool) -> None:
    """0. Induce and validate the structural profile, unless one already exists."""
    if os.path.exists(profile_path):
        _say("profile", "using existing %s" % profile_path)
        return
    if dry_run:
        _say("profile", "would induce one (~$0.02) -- none at %s" % profile_path)
        return
    _run("profile", [PYTHON, "induce_structure.py", corpus_path, "--out", profile_path,
                     "--model", model])


def gate_hygiene(corpus_path: str) -> dict:
    """0.5. Is the .txt a faithful rendering of the statute?

    This runs before anything looks at structure, and it is the only check in the build
    that can see its class of damage. Measured by wrecking Schedule 1 three ways: a
    hyphenated wrap corrupts 45 of 62 provision texts and a paginated rendering corrupts
    13, and BOTH still segment to exactly 62 provisions with an orphan rate under the
    ceiling. Provision count, orphan rate and reference resolution all pass on a file
    whose text is three-quarters mangled.

    It used to run only inside induce_structure, so a corpus that already had a profile
    never saw it -- which is precisely the case of re-running over a freshly downloaded
    file.
    """
    with io.open(corpus_path, encoding="utf-8") as fh:
        report = prep_corpus.hygiene(fh.read().splitlines())
    _say("hygiene", "%d lines (%d non-blank), mean %.1f words, %d dangling hyphen(s), "
                    "%d numeric-only line(s), %d repeated line(s)"
         % (report.lines, report.non_blank, report.mean_words, report.dangling_hyphens,
            report.numeric_only_lines, len(report.repeated_lines)))
    for warning in report.warnings:
        _say("hygiene", "note: %s" % warning.split(".")[0])
    if report.problems:
        raise GateFailed("\n".join(report.problems))
    return {"non_blank": report.non_blank}


def gate_segmentation(profile_path: str, corpus_path: str) -> dict:
    """1. Did the profile actually split the file?"""
    seg = prep_corpus.segmentation(profile_path, corpus_path)
    count = len(seg.provisions)
    rate = seg.interior_orphans / float(seg.non_blank or 1)
    _say("segment", "%d provisions, %d leading orphans, %d interior (%.1f%%)"
         % (count, seg.leading_orphans, seg.interior_orphans, 100 * rate))
    if count < MIN_PROVISIONS:
        raise GateFailed(
            "only %d provision(s) from %d non-blank lines. The profile's provision "
            "pattern did not match. This is what an empty graph looks like before it "
            "costs anything." % (count, seg.non_blank))
    if rate > MAX_INTERIOR_ORPHAN_RATE:
        raise GateFailed(
            "%.1f%% of lines sit after a provision has been seen but belong to none, "
            "above the %.0f%% ceiling. Some structural form is unrecognised and its text "
            "is being dropped." % (100 * rate, 100 * MAX_INTERIOR_ORPHAN_RATE))
    return {"provisions": count, "order": seg.order}


def gate_references(profile_path: str, corpus_path: str) -> dict:
    """2. Do citations resolve? The tier the graph exists to serve."""
    profile = prep_corpus.load_profile(profile_path)
    seg = prep_corpus.segmentation(profile_path, corpus_path)
    parser = references.ReferenceParser(profile, list(seg.provisions))
    per = {pid: parser.parse(text, pid) for pid, text in seg.provisions.items()}
    summary = references.summarise(per)
    with_edges = summary["provisions_with_references"] / float(len(seg.provisions) or 1)
    _say("refs", "%d internal, %d external, %d unresolved; %.1f%% resolution; "
                 "%d of %d provisions cite something"
         % (summary["internal"], summary["external"], summary["unresolved"],
            100 * summary["internal_resolution_rate"],
            summary["provisions_with_references"], len(seg.provisions)))
    if summary["internal_resolution_rate"] < MIN_REFERENCE_RESOLUTION:
        raise GateFailed(
            "only %.0f%% of internal-looking citations resolve. A statute that points at "
            "other Acts is normal; one that cannot resolve its own citations has a broken "
            "identifier pattern." % (100 * summary["internal_resolution_rate"]))
    if with_edges < MIN_PROVISIONS_WITH_EDGES:
        raise GateFailed(
            "only %.1f%% of provisions cite anything. Cross-referenced coverage is the "
            "only tier where the graph beat the control, so a corpus with no edges makes "
            "the graph pointless." % (100 * with_edges))
    return summary


def stage_extract(model: str, dry_run: bool, only: list) -> None:
    """3. SPIRES extraction. The first stage that costs money."""
    argv = [PYTHON, os.path.join("spires", "extract_corpus.py"), "--model", model]
    if only:
        argv += ["--only"] + only
    if dry_run:
        argv.append("--dry-run")
    _run("extract", argv)


def stage_clean(dry_run: bool) -> None:
    if dry_run:
        _say("clean", "would clean the extractions (free)")
        return
    _run("clean", [PYTHON, os.path.join("spires", "clean_extraction.py")])


def gate_modalities() -> dict:
    """4. Did the schema land? Every norm NONE means it did not."""
    import glob
    import yaml
    counts = {}
    total = 0
    for path in glob.glob(corpus_config.provision_glob(".clean.yaml")):
        document = yaml.safe_load(io.open(path, encoding="utf-8")) or {}
        for norm in (document.get("norms") or []):
            modality = (norm or {}).get("deontic_modality") or "UNSPECIFIED"
            counts[modality] = counts.get(modality, 0) + 1
            total += 1
    if not total:
        raise GateFailed("no norms in any cleaned extraction -- nothing was extracted.")
    top, top_count = max(counts.items(), key=lambda kv: kv[1])
    _say("modality", "%d norms: %s" % (total, ", ".join(
        "%s %d" % (k, v) for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))))
    if top_count / float(total) > MAX_SINGLE_MODALITY_SHARE:
        raise GateFailed(
            "%.0f%% of norms are %s. The schema's distinctions did not land -- check the "
            "worked examples in %s." % (100 * top_count / total, top,
                                        corpus_config.SPIRES_SCHEMA))
    return counts


def gate_prefix() -> None:
    """5. Is this corpus allowed to write under its node prefix?"""
    problems = graph_registry.check(corpus_config.NAME, corpus_config.NODE_PREFIX)
    _say("prefix", "%s wants %s" % (corpus_config.NAME, corpus_config.NODE_PREFIX))
    if problems:
        raise GateFailed("\n".join(problems))


def gate_auth(model: str) -> None:
    """5.5. Does the key actually work, and is the model reachable with it?

    Free: listing models is not a generation and bills nothing. It runs in --dry-run too,
    because "every free stage and every gate" is what that flag promises and this is the
    one gate that stands between a stale key and an hour of failures.

    Presence is not validity, and every check upstream of here only tested presence.
    check_environment reported `ok OPENAI_API_KEY set, 164 chars` for a revoked key; the
    build then authenticated 106 times, failed 106 times at ~10s each, wrote one stanza
    per failure into errors.log, and reported nothing until the whole run was over. The
    provisions were all still marked `to do`, so nothing downstream noticed either.
    """
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
    if not key:
        raise GateFailed(
            "No OPENAI_API_KEY or OPENAI_KEY. Put one in .env (see .env.example), or "
            "dot-source setup_env.ps1.")
    try:
        from openai import OpenAI
    except ImportError:
        _say("auth", "openai package not importable here; skipping the liveness check")
        return
    try:
        ids = {m.id for m in OpenAI(api_key=key.strip()).models.list().data}
    except Exception as exc:  # noqa: BLE001 - any failure here must stop the run
        raise GateFailed(
            "the LLM key was rejected: %s: %s\nThe key is present but not valid. Check "
            "which one is reaching the process -- OPENAI_API_KEY wins over OPENAI_KEY, "
            "and a stale OPENAI_KEY in the user environment outranks nothing at all."
            % (type(exc).__name__, str(exc).split(chr(10))[0][:160]))
    _say("auth", "key accepted, %d models visible" % len(ids))
    if model not in ids:
        raise GateFailed(
            "%r is not available to this key. Visible models include: %s"
            % (model, ", ".join(sorted(m for m in ids if m.startswith("gpt-4"))[:6])))


def stage_build(dry_run: bool) -> None:
    argv = [PYTHON, os.path.join("spires", "build_spires_graph.py")]
    if dry_run:
        argv.append("--dry-run")
    _run("build", argv)


# --- bootstrapping a corpus this checkout has never seen ------------------------------

def corpus_slug(corpus_path: str) -> str:
    """`APPartIIIC.txt` -> `appartiiic`. Names the profile and the config derived from it."""
    stem = os.path.splitext(os.path.basename(corpus_path))[0]
    return re.sub(r"[^0-9a-z]+", "_", stem.lower()).strip("_") or "corpus"


def existing_config_for(corpus_path: str):
    """A corpus.*.yaml in this checkout whose `paths.raw` is this file, if there is one."""
    import glob
    try:
        import yaml
    except ImportError:
        return None
    target = os.path.abspath(corpus_path)
    for candidate in sorted(glob.glob(os.path.join(ROOT, "corpora", "corpus*.yaml"))):
        try:
            with io.open(candidate, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
        except Exception:  # noqa: BLE001 - a malformed config is simply not a match
            continue
        raw = ((loaded.get("paths") or {}).get("raw") or "")
        if raw and os.path.abspath(os.path.join(ROOT, raw)) == target:
            return candidate
    return None


def bootstrap(corpus_path: str, args):
    """Make sure the configured corpus IS the one that was asked for.

    Without this, `build_corpus.py MyStatute.txt` ran the configured corpus's profile and
    node prefix over a different file. The segmentation gate caught it -- but reported
    "the profile's provision pattern did not match", which blames the pattern when the
    real problem is that it is Schedule 1's pattern applied to another Act.

    Returns None to carry on in this process, or an exit code when it has re-executed.
    Re-execution is necessary rather than tidy: corpus_config reads its file at import,
    and the gates run in-process, so a config chosen after import would be ignored by
    exactly the checks that matter.
    """
    if os.path.abspath(corpus_path) == os.path.abspath(corpus_config.RAW_PATH):
        return None                                   # already the configured corpus

    slug = corpus_slug(corpus_path)
    # Look for a config by what it POINTS AT, not by what it is called. corpus.yaml files
    # are named after the instrument and corpora after the download, so
    # corpus.part_iiic.yaml and APPartIIIC.txt belong together and no filename rule would
    # pair them. Without this, a corpus that already had a config got a second one.
    config_path = existing_config_for(corpus_path) or os.path.join(
        ROOT, "corpora", "corpus.%s.yaml" % slug)
    profile_path = os.path.join(ROOT, "profiles", "%s.yaml" % slug)

    if os.environ.get("_KG_BOOTSTRAPPED"):
        # Re-executed once already and still pointing elsewhere: refuse rather than loop.
        raise GateFailed(
            "%s is configured for %r, but %s was asked for. Set CORPUS_CONFIG, or fix "
            "`paths.raw` in that file." % (corpus_config.CONFIG_PATH, corpus_config.NAME,
                                           corpus_path))

    if not os.path.exists(config_path):
        _say("bootstrap", "%s is a corpus this checkout has no config for" % corpus_path)
        if args.dry_run:
            _say("bootstrap", "would induce %s and write %s (~$0.02), then re-run"
                 % (os.path.relpath(profile_path, ROOT), os.path.relpath(config_path, ROOT)))
            return 0
        argv = [PYTHON, "induce_structure.py", corpus_path,
                "--out", profile_path, "--model", args.model,
                "--write-config", config_path]
        _run("bootstrap", argv)

    _say("bootstrap", "re-running with %s" % os.path.relpath(config_path, ROOT))
    env = dict(os.environ)
    env["CORPUS_CONFIG"] = config_path
    env["_KG_BOOTSTRAPPED"] = "1"
    return subprocess.run([PYTHON, os.path.abspath(__file__)] + sys.argv[1:],
                          cwd=ROOT, env=env).returncode


# --- driver -------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Take a plain-text statute to a knowledge graph, gate by gate.")
    ap.add_argument("corpus", nargs="?", default=None,
                    help="The statute. Defaults to the configured corpus.")
    ap.add_argument("--profile", default=None, help="Structural profile to use or write.")
    ap.add_argument("--model", default="gpt-4o")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run every free stage and every gate; stop before spending.")
    ap.add_argument("--only", nargs="*", default=None,
                    help="Extract only these provisions. For a pilot.")
    ap.add_argument("--skip-verify", action="store_true",
                    help="Skip the exception-recovery pass, which costs one call per norm.")
    args = ap.parse_args()

    corpus_path = args.corpus or corpus_config.RAW_PATH
    if not os.path.exists(corpus_path):
        print("No such corpus: %s" % corpus_path, file=sys.stderr)
        return 2

    if not args.profile:
        bootstrapped = bootstrap(corpus_path, args)
        if bootstrapped is not None:
            return bootstrapped
    profile_path = args.profile or corpus_config.PROFILE_PATH

    started = time.time()
    print("corpus  : %s" % corpus_path)
    print("profile : %s" % profile_path)
    print("prefix  : %s" % corpus_config.NODE_PREFIX)
    print()

    try:
        stage_profile(corpus_path, profile_path, args.model, args.dry_run)
        if not os.path.exists(profile_path):
            _say("profile", "no profile yet; stopping here (dry run)")
            return 0
        gate_hygiene(corpus_path)
        gate_segmentation(profile_path, corpus_path)
        gate_references(profile_path, corpus_path)
        gate_prefix()
        gate_auth(args.model)

        stage_extract(args.model, args.dry_run, args.only)
        if args.dry_run:
            print("\nDry run: stopped before spending. Everything above is free and passed.")
            return 0

        stage_clean(args.dry_run)
        gate_modalities()
        if not args.skip_verify:
            _run("verify", [PYTHON, os.path.join("spires", "verify_exceptions.py"),
                            "--model", args.model])
        stage_build(args.dry_run)
    except GateFailed as exc:
        print("\nSTOPPED: %s" % exc, file=sys.stderr)
        print("Nothing further was run. Fix the stage above and start again.", file=sys.stderr)
        return 1

    print("\ndone in %.0fs. Generate from it with:" % (time.time() - started))
    print("    python -m kg_testgen --provision <id> --context-mode spires")
    print("    python -m kg_testgen --list-provisions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
