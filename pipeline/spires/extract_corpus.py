"""Run SPIRES extraction over every provision of the configured corpus, resumably.

THE METHOD. SPIRES -- Structured Prompt Interrogation and Recursive Extraction of
Semantics (Caufield et al., Bioinformatics 40(3):btae104, 2024,
doi:10.1093/bioinformatics/btae104) -- populates a knowledge base by interrogating an LLM
against a user-supplied schema, recursing into each nested slot rather than asking for a
whole structure in one call. This script is a driver around its reference implementation,
OntoGPT; the authors ask that the SPIRES paper be cited for both. See REFERENCES.md.

Two departures from the paper's setting are worth stating, because they are this
project's adaptation rather than the method's:

  * SPIRES was developed for biomedical text and grounds matched terms against
    ontologies. There is no ontology to ground legislative actors or acts against, so the
    NamedEntity slots in spires/norm.yaml carry no `annotators` and no grounding is
    attempted -- the schema alone does the work.
  * The recursion the paper describes is why cost is more than one call per provision:
    SPIRES issues a root call and then a further call for each CompoundExpression slot it
    has to fill. Only the root prompt is recorded in the output YAML.

MEASURED, and it does not match what estimate_cost() assumes. The recorded root prompts
across the 106 Part IIIC provisions average 223 tokens (min 77, max 528), while
estimate_cost bills the whole 2,373-token schema FILE against every call -- a 10.6x
over-count on that component, because the file's YAML scaffolding, licence, prefixes and
top-level description never reach any prompt. SPIRES sends the slot descriptions of the
class being extracted, nothing more.

The estimate nonetheless lands near the measured spend, which means the over-count is
being cancelled by an under-count elsewhere -- almost certainly RECURSION_MULTIPLIER,
since each recursive call carries its own unmodelled prompt. Both numbers are wrong in
opposite directions. Treat the total as calibrated and the breakdown as not yet
established; see the note in estimate_cost().

A hundred extractions at roughly half a minute each is long enough that a bash loop is
the wrong tool: a network blip a third of the way through should not cost the whole run.
Each clause is written as it completes and skipped if already present, so re-running
after a failure resumes rather than restarts.

Provisions, not the flattened corpus documents: a provision maps 1:1 onto the :Provision
nodes the query method uses, whereas the flattening in evaluation/arms/prep_app.py splits
some clauses across several self-contained sentences and would produce several
disconnected extractions for one provision.

    python spires/extract_corpus.py --dry-run
    python spires/extract_corpus.py
    python spires/extract_corpus.py --only 6.1 6.2
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import corpus_config  # noqa: E402
import prep_corpus  # noqa: E402

SCHEMA = corpus_config.SPIRES_SCHEMA
INPUT_DIR = corpus_config.SPIRES_INPUT_DIR
OUTPUT_DIR = corpus_config.SPIRES_OUTPUT_DIR
def find_ontogpt():
    """Where the ontogpt executable is.

    Was the literal `.venv-ontogpt/Scripts/ontogpt.exe`, which is Windows-only, relative
    to the working directory, and silently wrong everywhere else -- the failure is a
    FileNotFoundError from subprocess with no indication that a virtualenv is missing.

    ONTOGPT overrides everything, for a system install or an unusual layout.
    """
    override = os.environ.get("ONTOGPT")
    if override:
        return override
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for venv in (".venv-ontogpt", ".venv"):
        for sub, name in (("Scripts", "ontogpt.exe"), ("bin", "ontogpt")):
            candidate = os.path.join(root, venv, sub, name)
            if os.path.exists(candidate):
                return candidate
    found = shutil.which("ontogpt")
    return found or os.path.join(root, ".venv-ontogpt", "Scripts", "ontogpt.exe")


PYTHON = find_ontogpt()



def write_inputs(clauses):
    """One file per clause, so ontogpt's -i has something to point at."""
    os.makedirs(INPUT_DIR, exist_ok=True)
    written = {}
    for pid, text in clauses.items():
        path = os.path.join(INPUT_DIR, corpus_config.provision_filename(pid, ".txt"))
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("%s %s\n" % (pid, text) if not text.startswith(pid) else text + "\n")
        written[pid] = path
    return written


# gpt-4o and gpt-4o-mini list prices, USD per 1M tokens.
PRICES = {"gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.150, 0.600)}

# SPIRES is recursive: a root call, then one more for each CompoundExpression slot it has
# to fill. Measured on Schedule 1 the whole run came to ~$0.90 against a single-call floor
# of $0.59, so it makes about half as many calls again as there are provisions.
RECURSION_MULTIPLIER = 1.5

# Completion length, measured across the 62 Schedule 1 extractions.
COMPLETION_TOKENS = 400


def estimate_cost(texts, model):
    """What this run will cost, priced from the prompt rather than from a flat rate.

    The previous estimate was `count x $0.042`, calibrated on an early 24-provision pilot.
    It over-stated Schedule 1 by 2.9x -- $2.60 against a measured $0.90 -- and projected
    $4.45 for Part IIIC, which reads as though the second corpus were five times as
    expensive. It is not: 1.71x the provisions, at ~$1.53.

    The shape that matters is that the schema is resent with every call and is 2,107
    tokens against a mean provision of ~100, so cost tracks the number of PROVISIONS, not
    the size of the corpus. Part IIIC has 1.44x Schedule 1's words but 1.71x its
    provisions, and costs 1.7x.
    """
    try:
        import tiktoken
        encoder = tiktoken.get_encoding("cl100k_base")
        count = lambda t: len(encoder.encode(t))      # noqa: E731
        how = "tiktoken"
    except ImportError:
        count = lambda t: max(1, len(t) // 4)         # noqa: E731
        how = "chars/4"

    if not texts:
        return 0.0, "nothing to do"
    with io.open(SCHEMA, encoding="utf-8") as fh:
        schema_tokens = count(fh.read())
    price_in, price_out = PRICES.get(model, PRICES["gpt-4o"])
    prompt_tokens = sum(schema_tokens + count(t) for t in texts)
    completion_tokens = len(texts) * COMPLETION_TOKENS
    floor = (prompt_tokens * price_in + completion_tokens * price_out) / 1e6
    return floor * RECURSION_MULTIPLIER, (
        "%d provisions x (%d schema + ~%d text) tokens, x%.1f for recursion, via %s"
        % (len(texts), schema_tokens,
           (prompt_tokens / max(1, len(texts))) - schema_tokens,
           RECURSION_MULTIPLIER, how))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-4o")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--only", nargs="*", default=None, help="Specific provision ids.")
    args = ap.parse_args()

    # Segmented from the structural profile, not from a parser written for this one
    # statute. Keys are bare identifiers -- '6.1', '26WE(1)' -- and the order is the
    # order the instrument states them in.
    clauses = prep_corpus.provisions()
    inputs = write_inputs(clauses)
    if args.only:
        inputs = {k: v for k, v in inputs.items() if k in set(args.only)}

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    todo = []
    # Document order from the segmentation. The previous key, [int(x) for x in
    # pid.split(".")], assumed every statute numbers clauses N.M and raises on "26WE(1)".
    for pid in [p for p in prep_corpus.order() if p in inputs]:
        path = inputs[pid]
        out = os.path.join(OUTPUT_DIR, corpus_config.provision_filename(pid, ".yaml"))
        if os.path.exists(out) and not args.overwrite:
            continue
        todo.append((pid, path, out))

    print("%d clause(s) total, %d already extracted, %d to do"
          % (len(inputs), len(inputs) - len(todo), len(todo)))
    if args.dry_run:
        estimate, detail = estimate_cost([clauses[pid] for pid, _p, _o in todo], args.model)
        print("estimated cost: $%.2f on %s   (%s)" % (estimate, args.model, detail))
        return 0

    if not os.path.exists(PYTHON) and not shutil.which(PYTHON):
        print("ontogpt not found at %s.\n"
              "Run bootstrap.ps1 to create the environments, or set ONTOGPT to the "
              "executable." % PYTHON, file=sys.stderr)
        return 1

    # build_corpus.py loads this too and subprocesses inherit it, but this script is also
    # run on its own to resume a partial extraction, and then nothing else would.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    env = dict(os.environ)
    key = env.get("OPENAI_API_KEY") or env.get("OPENAI_KEY")
    if not key:
        print("No OPENAI_API_KEY or OPENAI_KEY in the environment.", file=sys.stderr)
        return 1
    # OPENAI_API_KEY wins, and is written back over whatever ontogpt would have read.
    # Without this an ontogpt subprocess inheriting a stale OPENAI_KEY authenticates with
    # the stale one even when a good OPENAI_API_KEY is present.
    env["OPENAI_API_KEY"] = key
    env["OPENAI_KEY"] = key

    started, failed = time.time(), []
    for index, (pid, path, out) in enumerate(todo, 1):
        result = subprocess.run(
            [PYTHON, "extract", "-t", SCHEMA, "-i", path, "-m", args.model, "-o", out],
            env=env, capture_output=True, text=True)
        ok = result.returncode == 0 and os.path.exists(out)
        if not ok:
            failed.append(pid)
            # Keep the stderr tail: a schema error looks nothing like a rate limit, and
            # the difference decides whether re-running will help.
            with io.open(os.path.join(OUTPUT_DIR, "errors.log"), "a", encoding="utf-8") as fh:
                fh.write("=== %s ===\n%s\n\n" % (pid, (result.stderr or "")[-1500:]))
        elapsed = time.time() - started
        print("[%2d/%2d] %-6s %-4s  %.0fs elapsed, ~%.0fs left"
              % (index, len(todo), pid, "ok" if ok else "FAIL", elapsed,
                 elapsed / index * (len(todo) - index)))

    print("\ndone: %d extracted, %d failed %s"
          % (len(todo) - len(failed), len(failed), failed or ""))
    if failed:
        print("see %s; re-run to retry only the failures"
              % os.path.join(OUTPUT_DIR, "errors.log"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
