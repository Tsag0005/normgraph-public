"""Is this checkout able to run the pipeline? Reports without changing anything.

    python check_environment.py            # everything, including optional pieces
    python check_environment.py --quiet    # only what is wrong

Distinct from check_setup.py, which preflights the EDC construction path -- its dataset,
its few-shot files -- and belongs to a comparison arm rather than to the pipeline.

Nothing here makes an API call or writes a file. Secrets are checked for presence and
shape only; no value is ever printed.
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

OK, WARN, FAIL = "ok", "warn", "FAIL"

# Import name -> the requirements file that provides it.
PIPELINE_MODULES = {
    "neo4j": "requirements/pipeline.txt",
    "openai": "requirements/pipeline.txt",
    "dotenv": "requirements/pipeline.txt",
    "yaml": "requirements/pipeline.txt",
    "tiktoken": "requirements/pipeline.txt",
    "truststore": "requirements/pipeline.txt",
}
EVALUATION_MODULES = {"docx": "requirements/evaluation.txt"}


class Report:
    def __init__(self):
        self.rows = []

    def add(self, status, label, detail=""):
        self.rows.append((status, label, detail))

    def show(self, quiet=False):
        for status, label, detail in self.rows:
            if quiet and status == OK:
                continue
            print("  %-5s %-40s %s" % (status, label, detail))

    @property
    def failures(self):
        return [r for r in self.rows if r[0] == FAIL]


def check_modules(report, modules, optional_label=None):
    for name, source in sorted(modules.items()):
        try:
            module = importlib.import_module(name)
        except ImportError:
            status = WARN if optional_label else FAIL
            report.add(status, "import %s" % name, "missing -- pip install -r %s" % source)
        else:
            version = getattr(module, "__version__", "")
            report.add(OK, "import %s" % name, version)


def check_corpus(report):
    try:
        import corpus_config
        import prep_corpus
    except Exception as exc:  # noqa: BLE001
        report.add(FAIL, "corpus configuration", "%s: %s" % (type(exc).__name__, exc))
        return
    report.add(OK, "corpus", "%r (%s)" % (corpus_config.NAME, corpus_config.CONFIG_PATH))
    for label, path in (("statute", corpus_config.RAW_PATH),
                        ("profile", corpus_config.PROFILE_PATH),
                        ("schema", corpus_config.SPIRES_SCHEMA)):
        report.add(OK if os.path.exists(path) else FAIL, label,
                   path if os.path.exists(path) else "missing: %s" % path)
    try:
        provisions = prep_corpus.provisions()
        report.add(OK if provisions else FAIL, "segmentation",
                   "%d provisions" % len(provisions))
    except SystemExit as exc:
        report.add(FAIL, "segmentation", str(exc).splitlines()[0])
    except Exception as exc:  # noqa: BLE001
        report.add(FAIL, "segmentation", "%s: %s" % (type(exc).__name__, exc))


def check_ontogpt(report):
    """The extraction step shells out to it, so a missing one is a runtime failure."""
    sys.path.insert(0, os.path.join(ROOT, "spires"))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "extract_corpus", os.path.join(ROOT, "spires", "extract_corpus.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        path = module.PYTHON
    except Exception as exc:  # noqa: BLE001
        report.add(WARN, "ontogpt", "could not resolve: %s" % exc)
        return
    found = os.path.exists(path) or shutil.which(path)
    report.add(OK if found else WARN, "ontogpt",
               path if found else "not found at %s -- run bootstrap.ps1, or set ONTOGPT" % path)


def check_secrets(report, live=False):
    """Presence and shape only, unless --live. No value is printed, here or anywhere."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
    if not key:
        report.add(WARN, "OPENAI_API_KEY",
                   "not set -- needed only for extraction and generation")
    elif not key.startswith("sk-"):
        report.add(WARN, "OPENAI_API_KEY", "set, but does not begin 'sk-'")
    elif not live:
        # Presence is not validity. A revoked key reports exactly like a good one here,
        # and the build then fails authentication once per provision. --live settles it
        # for free; build_corpus.py gates on the same call before it spends anything.
        report.add(OK, "OPENAI_API_KEY", "set, %d chars (not verified -- pass --live)"
                   % len(key))
    else:
        try:
            from openai import OpenAI
            models = {m.id for m in OpenAI(api_key=key.strip()).models.list().data}
        except Exception as exc:  # noqa: BLE001
            report.add(FAIL, "OPENAI_API_KEY", "REJECTED: %s: %s"
                       % (type(exc).__name__, str(exc).splitlines()[0][:120]))
        else:
            missing = [m for m in ("gpt-4o", "gpt-4o-mini") if m not in models]
            report.add(FAIL if missing else OK, "OPENAI_API_KEY",
                       "accepted, %d models; MISSING %s" % (len(models), ", ".join(missing))
                       if missing else "accepted, %d models visible" % len(models))

    neo4j = {k: os.environ.get(k) for k in
             ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")}
    missing = [k for k, v in neo4j.items() if not v]
    if not missing:
        report.add(OK, "Neo4j credentials", "all four set")
    elif len(missing) == 4:
        report.add(WARN, "Neo4j credentials",
                   "none set -- dot-source setup_env.ps1, or put them in .env")
    else:
        report.add(FAIL, "Neo4j credentials", "partially set; missing %s" % ", ".join(missing))

    # On Aura the database is named after the instance, not "neo4j". This costs an hour
    # to discover from `DatabaseNotFound: Unable to get a routing table for database`.
    uri, database = neo4j["NEO4J_URI"], neo4j["NEO4J_DATABASE"]
    if uri and database and ".databases.neo4j.io" in uri and database == "neo4j":
        report.add(WARN, "NEO4J_DATABASE",
                   "is 'neo4j' on an Aura URI -- Aura names the database after the "
                   "instance id, and connecting to 'neo4j' fails")


def main() -> int:
    ap = argparse.ArgumentParser(description="Check this checkout can run the pipeline.")
    ap.add_argument("-q", "--quiet", action="store_true", help="Only show what is wrong.")
    ap.add_argument("--live", action="store_true",
                    help="Also verify the LLM key against the API. Free -- listing "
                         "models is not a generation and bills nothing.")
    args = ap.parse_args()

    report = Report()
    print("pipeline")
    check_modules(report, PIPELINE_MODULES)
    check_corpus(report)
    check_ontogpt(report)
    check_secrets(report, live=args.live)
    check_modules(report, EVALUATION_MODULES, optional_label="evaluation")
    report.show(args.quiet)

    print()
    if report.failures:
        print("%d problem(s). Run bootstrap.ps1, or see pipeline/PIPELINE_RUNBOOK.md section 1."
              % len(report.failures))
        return 1
    print("Ready. `python pipeline/build_corpus.py --dry-run` runs every free gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
