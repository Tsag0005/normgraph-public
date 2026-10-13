"""Second-pass recovery of exception clauses that SPIRES' recursive extraction misses.

SPIRES splits the first pass's `norms:` line on semicolons and re-extracts each fragment
on its own. The fragment carries the "unless" clause, but the sub-extractor fills the
easy slots (modality, bearer, action, object) and frequently leaves `exceptions` empty --
APP 6.1 came back with one of its two exceptions, and several provisions with none.

Rather than fight the two-pass architecture, this adds a third pass that asks one narrow
question per norm, about a single sentence, with nothing else to do. Narrow questions are
answered far more reliably than "fill in this eight-field object".

The distinction this pass exists to protect:

    "must not use or disclose ... UNLESS the individual has consented"
        -> consent is an EXCEPTION; the norm exists and is switched off

    "Subclause 2.1 does not apply IF it is impracticable ..."
        -> impracticability is a CONDITION on a DISAPPLICATION; the whole norm IS the
           switching-off, so its trigger is not itself an exception

Getting that backwards would inflate exception counts with what are really conditions,
which is why the norm's modality is given to the model and DISAPPLICATION is called out.

    python spires_pilot/verify_exceptions.py --dry-run
    python spires_pilot/verify_exceptions.py
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_config  # noqa: E402
from clean_extraction import clean_provision, norm_sentences, unwrap  # noqa: E402

# Same stale spires_pilot default as clean_extraction had.
OUT_GLOB = corpus_config.provision_glob(".yaml")
_NORMS_LINE = re.compile(r"^norms:\s*(.+?)(?=^\w+:|\Z)", re.M | re.S)

PROMPT = """\
You are checking one sentence from Australian privacy legislation for an exception clause.

THE SENTENCE
------------
{sentence}

This sentence has already been classified as a {modality}.

WHAT COUNTS AS AN EXCEPTION
---------------------------
An exception is a circumstance that switches the rule OFF. It is normally introduced by
"unless", "other than", "except", or "but not". Example: in "the entity must not disclose
the information unless the individual has consented", the consent clause is an exception.

WHAT DOES NOT COUNT
-------------------
A condition is a circumstance that must hold for the rule to apply in the first place,
normally introduced by "if" or "where". Conditions are NOT exceptions.

{modality_note}

YOUR TASK
---------
Answer the fields in order. Decide what the clause DOES before deciding what it is.

Reply with JSON only:
{{"clause_found": "quote any candidate clause verbatim from the sentence, or null",
  "what_it_does": "in one sentence: does this clause switch the rule off, or set out when \
the rule applies?",
  "exceptions": [
     {{"statement": "verbatim text of the exception clause",
       "source_provision": "the subclause it points to, e.g. \\"6.2\\", or null"}}
  ]}}

Return an empty list for "exceptions" when the sentence contains none. Most sentences
contain none, and an empty list is the correct answer far more often than not.
"""

_DISAPPLICATION_NOTE = """\
IMPORTANT: this sentence is a DISAPPLICATION -- its whole function is to switch some
other provision off. The "does not apply if ..." clause is therefore its CONDITION, not
an exception to it. Unless there is a further "unless" nested inside, return an empty
list."""

_ORDINARY_NOTE = """\
This sentence states a rule of its own, so an "unless" clause in it is a genuine
exception."""


def verify_norm(client, model, sentence, modality):
    note = _DISAPPLICATION_NOTE if modality == "DISAPPLICATION" else _ORDINARY_NOTE
    prompt = PROMPT.format(sentence=sentence, modality=modality or "rule",
                           modality_note=note)
    completion = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}],
        temperature=0.0, response_format={"type": "json_object"})
    usage = completion.usage
    return (json.loads(completion.choices[0].message.content),
            usage.prompt_tokens, usage.completion_tokens)


def merge_exceptions(norm, found, sentence):
    """Add verified exceptions, skipping duplicates and unquotable claims."""
    existing = norm.setdefault("exceptions", [])
    seen = {(unwrap(e.get("statement")) or "").strip().lower() for e in existing}
    added = 0
    for item in found:
        statement = (item.get("statement") or "").strip()
        if not statement or statement.strip().lower() in seen:
            continue
        # Anti-fabrication: the exception must actually be in the sentence it came from.
        if " ".join(statement.lower().split()) not in " ".join(sentence.lower().split()):
            continue
        entry = {"statement": statement, "verified": True}
        if item.get("source_provision"):
            entry["source_provision"] = str(item["source_provision"])
        existing.append(entry)
        seen.add(statement.lower())
        added += 1
    if not existing:
        norm.pop("exceptions", None)
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default=OUT_GLOB)
    ap.add_argument("--model", default="gpt-4o")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    paths = sorted(p for p in glob.glob(args.glob)
                   if not p.endswith((".clean.yaml", ".verified.yaml")))
    if args.dry_run:
        total = 0
        for path in paths:
            document = yaml.safe_load(io.open(path, encoding="utf-8"))
            total += len(norm_sentences(document.get("raw_completion_output")))
        print("%d norm(s) across %d provisions -> %d calls, roughly $%.2f"
              % (total, len(paths), total, total * 0.0035))
        return 0

    from dotenv import load_dotenv
    load_dotenv()
    import truststore
    truststore.inject_into_ssl()
    from openai import OpenAI
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
    client = OpenAI(api_key=key)

    tin = tout = calls = 0
    print("%-6s %-6s %-9s %-9s %s" % ("prov", "norms", "excs before", "after", "added"))
    print("-" * 56)
    for path in paths:
        document = yaml.safe_load(io.open(path, encoding="utf-8"))
        extracted = document.get("extracted_object") or {}
        norms = [n for n in (extracted.get("norms") or []) if isinstance(n, dict)]
        sentences = norm_sentences(document.get("raw_completion_output"))

        if len(sentences) != len(norms):
            # Pairing is positional, so a count mismatch means the mapping is unsafe.
            print("%-6s SKIPPED - %d sentences vs %d norms, cannot pair"
                  % (extracted.get("provision_id"), len(sentences), len(norms)))
            continue

        before = sum(len(n.get("exceptions") or []) for n in norms)
        added = 0
        for norm, sentence in zip(norms, sentences):
            modality = unwrap(norm.get("deontic_modality"))
            try:
                verdict, a, b = verify_norm(client, args.model, sentence, modality)
            except Exception as exc:  # noqa: BLE001 - one bad norm must not stop the pass
                print("  verify failed: %s" % exc)
                continue
            tin, tout, calls = tin + a, tout + b, calls + 1
            added += merge_exceptions(norm, verdict.get("exceptions") or [], sentence)

        after = sum(len(n.get("exceptions") or []) for n in norms)
        print("%-6s %-6d %-9d %-9d %s" % (extracted.get("provision_id"), len(norms),
                                          before, after, "+%d" % added if added else "-"))

        out_path = path.replace(".yaml", ".verified.yaml")
        with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            yaml.dump(clean_provision(extracted, sentences), fh, sort_keys=False,
                      allow_unicode=True, width=100)

    cost = tin / 1e6 * 2.50 + tout / 1e6 * 10.00
    print("-" * 56)
    print("%d calls, %s in / %s out tokens, $%.4f"
          % (calls, "{:,}".format(tin), "{:,}".format(tout), cost))
    print("wrote *.verified.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
