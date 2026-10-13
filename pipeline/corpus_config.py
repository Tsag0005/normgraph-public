"""Which legislation this checkout is pointed at.

One place for every path, filename prefix, node-label prefix and provision pattern that
was previously hard-coded to the Australian Privacy Principles. See corpus.yaml.

Two deliberate properties:

  Defaults reproduce the old behaviour exactly. Every value here matches what the
  constant it replaced held, so a checkout without corpus.yaml -- or one where PyYAML is
  not installed, which is true of some of the three virtualenvs -- behaves as before
  rather than failing at import time in the middle of a long extraction run.

  Paths resolve against this file, not the working directory. The scripts are run from
  several places (repo root, spires/, a venv's Scripts/) and os.path.join on a relative
  constant silently produced different files depending on where you stood.

Segmentation is no longer per-statute code. `profile:` names a structural profile --
an ordered list of line classifiers, which level is the provision, how an identifier is
composed -- that induce_structure.py produces from the text and prep_corpus.py applies.
Cross-references are no longer per-statute code either: references.py builds its
patterns from the profile's citation nouns, identifier pattern and id_template. What
remains hand-written is the profile itself, and induce_structure.py produces that from
the text.
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))

# One checkout is normally one corpus, and corpus.yaml says which. CORPUS_CONFIG points
# at a different one for a single command, so a second instrument can be built and
# inspected without editing a tracked file and remembering to change it back:
#
#     CORPUS_CONFIG=corpus.part_iiic.yaml python build_corpus.py APPartIIIC.txt --dry-run
#
# Read at import, like everything else here, so it must be set before Python starts.
CORPORA_DIR = os.path.join(ROOT, "corpora")

CONFIG_PATH = os.environ.get("CORPUS_CONFIG") or os.path.join(CORPORA_DIR, "corpus.yaml")
if not os.path.isabs(CONFIG_PATH):
    # A bare name is resolved against corpora/ first, so `CORPUS_CONFIG=corpus.part_iiic.yaml`
    # keeps working now that the configs live together with the texts they describe.
    beside_corpora = os.path.join(CORPORA_DIR, CONFIG_PATH)
    CONFIG_PATH = beside_corpora if os.path.exists(beside_corpora) else os.path.join(
        ROOT, CONFIG_PATH)

# Mirrors corpus.yaml. Kept in sync by hand, and used verbatim when the file or PyYAML
# is unavailable -- see the module docstring.
_DEFAULTS = {
    "name": "app_schedule1",
    "description": "Schedule 1 of the Privacy Act 1988 (Cth)",
    "file_prefix": "app",
    "node_prefix": "SP_",
    "paths": {
        # `corpus` and `edc_graph_dir` used to live here and pointed into the vendored EDC
        # tree. They were read only by the full-corpus and EDC comparison arms, never by
        # the pipeline, and they are now in evaluation/eval_config.yaml where the rest of
        # the measurement paths are. Nothing below leaves this directory.
        "raw": os.path.join("corpora", "APPschedule1.txt"),
        "spires_dir": "spires",
        "profile": os.path.join("profiles", "app_schedule1.yaml"),
        "schema": os.path.join("spires", "norm.yaml"),
    },
    "modality_overrides": {"6.6": {1: "NONE"}},
    "prompt": {
        # The newline and indent are load-bearing: this is substituted into a
        # hand-wrapped paragraph, and a single-line value re-flows the line it lands in.
        "citation_examples": ('"Australian Privacy Principle 6", "subclause 6.2", "the\n'
                              '   Privacy Act" or "as required by Australian law"'),
        "example_provision": "subclause 6.2",
        "provision_plural": "Australian subclauses",
    },
    "provision": {
        "pattern": r"\b(?:subclause\s+)?(\d{1,2}\.\d{1,2})\b",
        "key_prefix": "subclause",
    },
}


def _load():
    try:
        import yaml
    except ImportError:
        return _DEFAULTS
    if not os.path.exists(CONFIG_PATH):
        return _DEFAULTS
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - a malformed config must not break an extraction
        return _DEFAULTS
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in loaded.items() if k not in ("paths", "provision")})
    for section in ("paths", "provision", "prompt"):
        merged[section] = dict(_DEFAULTS[section])
        merged[section].update(loaded.get(section) or {})
    return merged


_CFG = _load()

NAME = _CFG["name"]
DESCRIPTION = _CFG["description"]
FILE_PREFIX = _CFG["file_prefix"]
NODE_PREFIX = _CFG["node_prefix"]

PROVISION_PATTERN = _CFG["provision"]["pattern"]
PROVISION_KEY_PREFIX = _CFG["provision"]["key_prefix"]
PROVISION_RE = re.compile(PROVISION_PATTERN, re.IGNORECASE)

# provision id -> {norm index (1-based): modality}. Read as strings and ints, since
# YAML will give "6.6" as a string key but 1 as an integer.
MODALITY_OVERRIDES = {str(pid): {int(i): m for i, m in overrides.items()}
                      for pid, overrides in (_CFG.get("modality_overrides") or {}).items()}

# Corpus-specific strings the prompt renders. See corpus.yaml.
PROMPT = dict(_DEFAULTS["prompt"])
PROMPT.update(_CFG.get("prompt") or {})


def path(key: str) -> str:
    """An absolute path for one of the configured locations."""
    value = _CFG["paths"][key]
    return value if os.path.isabs(value) else os.path.join(ROOT, value)


RAW_PATH = path("raw")
# CORPUS_PATH and EDC_GRAPH_DIR used to be here. They pointed into the vendored EDC tree
# and were read only by evaluation/arms/, so they now live in evaluation's own config.
# Their removal is what lets this file name nothing outside pipeline/.
SPIRES_DIR = path("spires_dir")
PROFILE_PATH = path("profile")

SPIRES_INPUT_DIR = os.path.join(SPIRES_DIR, "input")
SPIRES_OUTPUT_DIR = os.path.join(SPIRES_DIR, "output")
SPIRES_GRAPH_DIR = os.path.join(SPIRES_DIR, "graph")
# The LinkML schema SPIRES extracts against. Instrument-neutral since Stage 4a: its
# worked examples describe shapes rather than citing APP 2.2, 6.2 and 4.4, so a second
# corpus does not need its own copy.
SPIRES_SCHEMA = path("schema")


def detect_provision_id(text: str):
    """The provision identifier a piece of text is about, or None.

    Lives here rather than in kg_testgen.edc_bridge, where it used to sit next to the
    EDC entity extractor. It needs nothing but PROVISION_RE, and putting it beside the
    pattern it applies means the query pipeline no longer imports the EDC bridge -- and
    so no longer drags in transformers and numpy -- to answer a question about a regex.

    Prefers the first match, which in the corpus format is the clause being stated
    rather than a cross-reference to another clause.
    """
    match = PROVISION_RE.search(text)
    return match.group(1) if match else None


def provision_key(provision_id: str) -> str:
    """`6.1` -> `subclause 6.1`, the key form provisions are stored and looked up under.

    Was `kg_testgen.edc_bridge.provision_key`, which routed through `build_graph.normalise`
    -- a function whose real job is folding entity mentions onto canonical keys, complete
    with a synonym table and article stripping. None of that applies to an identifier, and
    depending on it meant the SPIRES construction path imported the EDC graph builder to
    format a string.

    Equivalence was checked rather than assumed: this reproduces the old function's output
    for all 62 provisions, bare and prefixed, which is what the :AppNode keys in the live
    graph were built with. See tests/test_references.py.
    """
    ident = " ".join(provision_id.strip().lower().split())
    prefix = PROVISION_KEY_PREFIX
    if prefix and not ident.startswith(prefix):
        ident = "%s %s" % (prefix, ident)
    return ident


_SLUG_RE = re.compile(r"[^0-9A-Za-z]+")


def provision_slug(provision_id: str) -> str:
    """`6.1` -> `6_1`, `26WE(1)` -> `26WE_1`.

    Was `provision_id.replace(".", "_")`, which is a rule about one numbering scheme.
    Part IIIC composes identifiers with parentheses, and while those are legal on NTFS
    they are hostile in a glob and in a shell. Any run of non-alphanumerics becomes a
    single underscore, which leaves every existing Schedule 1 filename unchanged --
    asserted in tests, since renaming 62 committed artefacts would be a silent rebuild.
    """
    return _SLUG_RE.sub("_", provision_id).strip("_")


def provision_filename(provision_id: str, suffix: str) -> str:
    """`6.1`, `.yaml` -> `app_6_1.yaml`. The dot is not filename-safe on Windows."""
    return "%s_%s%s" % (FILE_PREFIX, provision_slug(provision_id), suffix)


def provision_glob(suffix: str = ".yaml") -> str:
    """Every per-provision artefact of one kind, e.g. `.../output/app_*.clean.yaml`."""
    return os.path.join(SPIRES_OUTPUT_DIR, "%s_*%s" % (FILE_PREFIX, suffix))


def label(name: str) -> str:
    """`Provision` -> `SP_Provision`. Also used for relationship types."""
    return "%s%s" % (NODE_PREFIX, name)


def summary() -> str:
    return ("corpus %r (%s)\n  file prefix : %s_*\n  node prefix : %s\n"
            "  raw         : %s\n  profile     : %s\n  schema      : %s\n"
            "  provision   : %s"
            % (NAME, DESCRIPTION, FILE_PREFIX, NODE_PREFIX,
               RAW_PATH, PROFILE_PATH, SPIRES_SCHEMA, PROVISION_PATTERN))


if __name__ == "__main__":
    print(summary())
