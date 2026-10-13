"""Thin CLI entry point. All real work lives in the modules it calls."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import corpus_config  # noqa: E402

from .config import ConfigError, add_cli_arguments, config_from_args, secrets_from_env
from .neo4j_client import GraphUnavailable
from .llm import LLMError
from .pipeline import run as run_pipeline
from .errors import ContextError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kg-testgen",
        description="Generate Gherkin compliance test cases for a section of privacy "
                    "legislation, using a Neo4j knowledge graph for supplementary context.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", nargs="?", default=None,
                        help="Path to a file containing the legislation section, or '-' for "
                             "stdin. Omit it and use --provision instead.")
    parser.add_argument("--provision", metavar="ID", nargs="+",
                        help="Generate for these provisions of the configured corpus, e.g. "
                             "'6.1' or '26WE(1)'. Several may be given and each produces "
                             "its own run. The text comes from the segmentation, so no "
                             "file has to be made by hand.")
    parser.add_argument("--corpus", metavar="CONFIG",
                        help="Which corpus to generate from: a corpus config path, or a "
                             "bare name resolving to corpus.<name>.yaml. Equivalent to "
                             "setting CORPUS_CONFIG, which corpus_config reads at import "
                             "-- so this re-executes rather than mutating an import that "
                             "has already happened.")
    parser.add_argument("--list-provisions", action="store_true",
                        help="Print every provision identifier, in document order, and exit.")
    add_cli_arguments(parser)
    return parser


def _provision_text(identifier: str) -> str:
    """One provision's text, straight from the segmentation.

    Without this, generating for a clause meant hand-making a file under sections/ --
    which is why that directory holds eleven of them. It is also the difference between
    "a graph exists" and "test cases are ready to generate": the corpus already knows what
    every provision says, and asking the user to copy it into a file is asking them to
    reproduce work the pipeline has already done.

    The identifier is prefixed onto the text. Part IIIC's subsections do not carry their
    section number -- 26WE(1) reads "(1) This section applies if:" -- so without this the
    generator could not tell which provision it had been given.
    """
    import prep_corpus

    provisions = prep_corpus.provisions()
    text = provisions.get(identifier)
    if text is None:
        near = [p for p in provisions if p.startswith(identifier.split("(")[0])][:6]
        raise ConfigError(
            "No provision %r in corpus %r.%s\nRun --list-provisions to see them all."
            % (identifier, corpus_config.NAME,
               (" Did you mean: %s?" % ", ".join(near)) if near else ""))
    return text if text.startswith(identifier) else "%s %s" % (identifier, text)


def _read_input(path: str) -> str:
    if path == "-":
        text = sys.stdin.read()
        if not text.strip():
            raise ConfigError("No input received on stdin. Pass a file path or pipe text in.")
        return text
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise ConfigError("Could not read input file %r: %s" % (path, exc)) from exc
    if not text.strip():
        raise ConfigError("Input file %r is empty." % path)
    return text


def _reexec_for_corpus(args) -> int:
    """Re-run this command against a different corpus, or return None to carry on.

    corpus_config resolves CORPUS_CONFIG at import, and this module imported it several
    frames ago, so a corpus chosen on the command line cannot be applied in place -- the
    segmentation, node prefix and provision pattern are all already bound. build_corpus.py
    has the same constraint and solves it the same way.
    """
    import os
    import subprocess

    name = args.corpus
    target = name if name.endswith(".yaml") else "corpus.%s.yaml" % name
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isabs(target):
        # corpora/ first, where the configs live beside the statutes they describe; then
        # the pipeline root, so an explicit relative path still works.
        candidates = [os.path.join(root, "corpora", target), os.path.join(root, target)]
        target = next((c for c in candidates if os.path.exists(c)), candidates[0])
    if not os.path.exists(target):
        raise ConfigError(
            "No corpus config for %r. Looked in %s."
            % (name, os.path.join(root, "corpora")))
    if os.path.abspath(target) == os.path.abspath(corpus_config.CONFIG_PATH):
        return None                                   # already the configured corpus
    if os.environ.get("_KG_TESTGEN_CORPUS"):
        raise ConfigError(
            "re-executed for %s and still reading %s. Unset CORPUS_CONFIG and retry."
            % (target, corpus_config.CONFIG_PATH))

    env = dict(os.environ)
    env["CORPUS_CONFIG"] = target
    env["_KG_TESTGEN_CORPUS"] = "1"
    argv = [a for a in (sys.argv[1:] if len(sys.argv) > 1 else []) ]
    return subprocess.run([sys.executable, "-m", "kg_testgen"] + argv,
                          cwd=root, env=env).returncode


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.corpus:
        try:
            code = _reexec_for_corpus(args)
        except ConfigError as exc:
            print("Configuration error: %s" % exc, file=sys.stderr)
            return 2
        if code is not None:
            return code

    if args.list_provisions:
        import prep_corpus
        for identifier in prep_corpus.order():
            print(identifier)
        return 0

    try:
        config = config_from_args(args)
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(levelname)-8s %(name)-28s %(message)s",
        stream=sys.stderr,
    )

    # Loading a .env is a convenience; the environment still wins.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Resolve every input BEFORE asking for credentials or spending anything: a mistyped
    # provision id should not require a working Neo4j connection to discover, and in a
    # batch it should not be discovered after the earlier provisions have been paid for.
    try:
        if args.provision:
            targets = [(pid, _provision_text(pid), "%s:%s" % (corpus_config.NAME, pid))
                       for pid in args.provision]
        else:
            path = args.input or "-"
            targets = [(None, _read_input(path), None if path == "-" else path)]
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2

    secrets = secrets_from_env()
    failures, results = [], []
    for index, (pid, section_text, input_path) in enumerate(targets, 1):
        if len(targets) > 1:
            print("\n=== %d/%d  %s ===" % (index, len(targets), pid), file=sys.stderr)
        run_config = dataclasses.replace(config, provision_id=config.provision_id or pid)
        try:
            results.append((pid, run_pipeline(section_text, run_config, secrets,
                                              input_path=input_path)))
        except ConfigError as exc:
            print("Configuration error: %s" % exc, file=sys.stderr)
            failures.append((pid, 2))
        except GraphUnavailable as exc:
            print("Database error: %s" % exc, file=sys.stderr)
            failures.append((pid, 3))
        except ContextError as exc:
            print("Retrieval failed: %s" % exc, file=sys.stderr)
            failures.append((pid, 4))
        except LLMError as exc:
            print("LLM error: %s" % exc, file=sys.stderr)
            failures.append((pid, 5))

    print()
    for pid, result in results:
        if len(results) > 1 or len(targets) > 1:
            print("--- %s" % pid)
        print("Run directory : %s" % result.run_dir)
        print("Feature file  : %s" % result.feature_path)
        print("Run record    : %s" % result.record_path)
        print("KG augmented  : %s" % ("yes" if result.augmented else "NO (unaugmented)"))
        if result.structure_problems:
            print("Gherkin warnings: %s" % "; ".join(result.structure_problems))
    if failures:
        print("\n%d of %d failed: %s" % (len(failures), len(targets),
                                        ", ".join(str(p) for p, _c in failures)),
              file=sys.stderr)
        return failures[0][1]
    return 0


if __name__ == "__main__":
    sys.exit(main())
