"""Clean SPIRES output into something a graph loader can consume.

SPIRES prompts the model with `field: <description of what goes here>`, where the angle
brackets mark the slot. The model frequently keeps the brackets, and sometimes fills the
slot with the description itself rather than an answer -- in the first pilot 54 of 55
condition and exception values came back looking like `<Empty if the condition is stated
here.>`. Prompt wording reduces this but does not eliminate it, so the defect is handled
here rather than hoped away.

Three passes, all conservative -- when a value is ambiguous it is kept, not guessed at:

    unwrap      strip the angle brackets the model copied from the prompt
    denull      turn description echoes and null-ish words into a real absence
    merge       collapse norms that differ only in which exception they carry

    python spires/clean_extraction.py                    # writes *.clean.yaml
    python spires/clean_extraction.py --report           # summary table only
"""

from __future__ import annotations

import argparse
import glob
import io
import os
import re
import sys
import urllib.parse

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corpus_config  # noqa: E402

# Was hard-coded to spires_pilot/output, a path that has not existed since the
# directory was renamed -- it only ever worked because --glob was always passed.
OUT_GLOB = corpus_config.provision_glob(".yaml")

# Words a model reaches for instead of leaving a field blank.
_NULLISH = {"", "none", "null", "empty", "blank", "n/a", "na", "not applicable",
            "not specified", "unspecified", "<>", "false", "no"}
# "none" is null-ish in a free-text statement ("exceptions: None" means there are none)
# but NOT in deontic_modality, where NONE is a legitimate enum value meaning the
# provision commands nothing of its own -- the correct reading of APP 4.4. The enum is
# therefore exempted in clean_norm rather than by removing the word here, which would
# let a literal "None" survive as an exception statement.

# A value that is really the field's own description echoed back. These all start with a
# capitalised instruction verb or contain the giveaway phrase from the schema text.
_DESCRIPTION_TELLS = (
    "empty if", "blank when", "leave blank", "as stated in the text",
    "the identifier of the provision", "the condition in a single clause",
    "the exception in a single clause", "true\" if", "if the text signals",
)

_ANGLE = re.compile(r"^\s*<\s*(.*?)\s*>\s*$", re.DOTALL)

# "This subclause applies in relation to X if ..." provisions command nothing themselves;
# they supply the conditions under which an exception named by another provision is met.
# The extraction consistently calls them NONE, which buries them among genuinely inert
# clauses -- and APP 6.2's limbs are exactly the content elements 6.1-E13..E17 need. The
# rule below matched 23 of 23 such norms with one collision (6.6), overridden explicitly.
_NORMS_LINE = re.compile(r"^norms:\s*(.+?)(?=^\w+:|\Z)", re.M | re.S)
# Fallback alternation, matching what this pattern was hard-coded to before it became
# corpus-driven. Used only when the structural profile cannot be read.
_UNIT_FALLBACK = ("subclause", "principle")


def _structural_units(profile=None):
    """The words this instrument uses for "a provision of itself".

    Was the literal alternation (subclause|principle), which is Schedule 1's vocabulary
    and nothing else's. Part IIIC opens five provisions with "This section applies if",
    every one of which the literal pattern misses -- so five APPLICABILITY norms would be
    left labelled NONE, silently, on a corpus the rule was never told about.

    Taken from the profile's level names and its citation nouns together, because the two
    carry different halves of the vocabulary: "principle" and "clause" are what Schedule 1
    calls its levels, while "subclause" only ever appears there as a citation.
    """
    if profile is None:
        try:
            import prep_corpus
            profile = prep_corpus.load_profile()
        except Exception:  # noqa: BLE001 - a missing profile must not stop a cleaning run
            return _UNIT_FALLBACK
    units = {level.name.replace("_", " ") for level in profile.levels}
    units |= {noun.lower() for noun in profile.citation_nouns}
    units |= set(_UNIT_FALLBACK)
    # Longest first, so "subclause" is tried before "clause" and the alternation cannot
    # match the shorter word inside the longer one.
    return tuple(sorted(units, key=len, reverse=True))


def applicability_pattern(profile=None):
    """The "This <unit> applies ..." matcher for one instrument's vocabulary."""
    return re.compile(
        r"^\s*this\s+(?:%s)\s+applies\b"
        % "|".join(re.escape(unit) for unit in _structural_units(profile)), re.I)


_APPLICABILITY = applicability_pattern()

# provision id -> {norm index (1-based): modality}. Reserved for readings the grammar
# gets wrong. Schedule 1's 6.6 says the principle "applies AS IF the entity's primary
# purpose ...", which modifies another provision rather than disapplying it or gating an
# exception. Corpus-specific by nature, so it is declared in corpus.yaml, not here.
MANUAL_OVERRIDES = corpus_config.MODALITY_OVERRIDES


def bearer_names_provisions(norm, profile=None):
    """Is the party this norm binds actually a set of provisions?

    "Australian Privacy Principles 5 to 13 apply in relation to the information" has a
    bearer of the PROVISIONS, not of a party -- which is the whole difference between NONE
    and APPLICABILITY. An APPLICABILITY provision states when ITSELF is met, for another
    provision's exception to use; a provision saying that OTHER provisions apply commands
    nothing and turns other rules on.

    The test is a structural word plus a number, both taken from the profile: "principle"
    is one of Schedule 1's level names and "subclause" one of its citation nouns, while a
    bearer like "the entity described in paragraph (a)" has the word but no number and is
    correctly left alone. Checked against all 21 APPLICABILITY norms in Schedule 1 -- it
    fires on none of them.
    """
    bearer = (decode_bearer(norm.get("bearer")) or "").lower()
    if not bearer or not re.search(r"\d", bearer):
        return False
    return any(unit in bearer for unit in _structural_units(profile))


def decode_bearer(value):
    """SPIRES writes NamedEntity slots as `AUTO:Australian%20Privacy%20Principles`."""
    value = unwrap(value)
    if not isinstance(value, str):
        return None
    text = value.split(":", 1)[-1] if value.startswith("AUTO:") else value
    return " ".join(urllib.parse.unquote(text).split())


def relabel_modality(norm, sentence, provision_id=None, index=None):
    """Return the modality this norm should carry, given the sentence it came from."""
    override = MANUAL_OVERRIDES.get(str(provision_id), {}).get(index)
    if override:
        return override
    current = unwrap(norm.get("deontic_modality"))
    if current == "NONE" and sentence and _APPLICABILITY.match(sentence):
        return "APPLICABILITY"
    if current == "APPLICABILITY" and bearer_names_provisions(norm):
        # The promotion above has an inverse, and without it the rule only ever moves in
        # one direction. Neutralising the schema's worked examples made the extractor
        # label APP 4.4 APPLICABILITY -- three times, including after the abstract
        # description was sharpened and after it was given a synthetic contrast case
        # closely matching 4.4's own wording. The extraction is not wrong about the
        # structure, only about the label: it reports the bearer as "Australian Privacy
        # Principles 5 to 13", which is the answer.
        return "NONE"
    return current


def unwrap(value):
    """Strip one layer of the angle brackets SPIRES uses to mark a fill-in slot."""
    if not isinstance(value, str):
        return value
    match = _ANGLE.match(value)
    return match.group(1).strip() if match else value.strip()


def denull(value):
    """Return None for a value that is an absence dressed up as text."""
    if not isinstance(value, str):
        return value
    lowered = value.strip().lower()
    if lowered in _NULLISH:
        return None
    if any(tell in lowered for tell in _DESCRIPTION_TELLS):
        return None
    return value.strip() or None


def clean_scalar(value):
    return denull(unwrap(value))


def clean_compound(item):
    """Clean one Condition/Exemption object; return None if nothing survives."""
    if not isinstance(item, dict):
        cleaned = clean_scalar(item)
        return {"statement": cleaned} if cleaned else None
    out = {}
    for key, value in item.items():
        cleaned = clean_scalar(value)
        if cleaned is not None:
            out[key] = cleaned
    # A condition or exception with no statement carries no information.
    return out if out.get("statement") else None


def _fold(value):
    """Normalise a slot value for equality testing only.

    NamedEntity slots come back as `AUTO:use%20or%20disclose`, and the model varies
    articles and case freely ("entity" / "The entity"), so a literal comparison reports
    two spellings of one norm as two norms.
    """
    import urllib.parse
    if not isinstance(value, str):
        return value
    text = urllib.parse.unquote(value.split(":", 1)[-1] if value.startswith("AUTO:") else value)
    text = text.lower().strip()
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article):]
    return text


def norm_signature(norm):
    """What makes two norms the same rule.

    Deliberately excludes `exceptions`: SPIRES emits one norm per exception limb for a
    provision like 6.1, which is one prohibition with two ways out, not two prohibitions.
    Conditions are included -- two norms with different triggers really are different.
    """
    return (_fold(norm.get("deontic_modality")), _fold(norm.get("bearer")),
            _fold(norm.get("action")), _fold(norm.get("object")),
            tuple(sorted(_fold(c.get("statement", "")) for c in norm.get("conditions", []))))


def clean_norm(norm):
    if not isinstance(norm, dict):
        return None
    out = {}
    # deontic_modality is an enum, not free text: unwrap brackets but never denull it.
    modality = unwrap(norm.get("deontic_modality"))
    if modality:
        out["deontic_modality"] = modality
    for key in ("bearer", "action", "object", "scope_qualifier"):
        value = clean_scalar(norm.get(key))
        if value is not None:
            out[key] = value
    for key in ("conditions", "exceptions"):
        items = [clean_compound(x) for x in (norm.get(key) or [])]
        items = [x for x in items if x]
        if items:
            out[key] = items
    return out or None


def merge_norms(norms):
    """Collapse norms differing only in their exceptions, unioning those exceptions."""
    merged = {}
    order = []
    for norm in norms:
        sig = norm_signature(norm)
        if sig not in merged:
            merged[sig] = dict(norm)
            merged[sig].setdefault("exceptions", [])
            order.append(sig)
            continue
        seen = {e.get("statement") for e in merged[sig]["exceptions"]}
        for exception in norm.get("exceptions", []):
            if exception.get("statement") not in seen:
                merged[sig]["exceptions"].append(exception)
                seen.add(exception.get("statement"))
    out = []
    for sig in order:
        norm = merged[sig]
        if not norm.get("exceptions"):
            norm.pop("exceptions", None)
        out.append(norm)
    return out


def clean_provision(extracted, sentences=None):
    """Clean one provision. `sentences` are the first pass's per-norm sentences, needed
    for the applicability relabel; omit them and modality is left as extracted."""
    out = {"provision_id": clean_scalar(extracted.get("provision_id"))}
    raw_norms = list(extracted.get("norms") or [])
    if sentences and len(sentences) == len(raw_norms):
        for i, (n, sent) in enumerate(zip(raw_norms, sentences), 1):
            if isinstance(n, dict):
                n["deontic_modality"] = relabel_modality(n, sent, out["provision_id"], i)
    norms = [clean_norm(n) for n in raw_norms]
    out["norms"] = merge_norms([n for n in norms if n])
    for key in ("disapplies", "cross_references"):
        values = [clean_scalar(v) for v in (extracted.get(key) or [])]
        values = [v for v in values if v]
        if values:
            out[key] = values
    return out


def norm_sentences(raw_completion):
    """The per-norm sentences the first pass emitted, in the order SPIRES recursed."""
    match = _NORMS_LINE.search(raw_completion or "")
    if not match:
        return []
    line = match.group(1)
    for terminator in ("\ndisapplies:", "\ncross_references:", "\nprovision_id:"):
        line = line.split(terminator)[0]
    return [s.strip() for s in line.split(";") if s.strip()]


def _sentences(document):
    return norm_sentences(document.get("raw_completion_output"))


def is_raw_extraction(path):
    """True only for a first-generation extraction, e.g. `app_6_1.yaml`.

    The glob is `app_*.yaml`, which also catches `app_6_1.clean.yaml` and
    `app_6_1.verified.yaml`. Excluding only `.clean.yaml` -- which is what this used to
    do -- left the verified files in, so a second run produced a third generation,
    `app_6_1.verified.clean.yaml`, 61 of which have now been committed by accident twice.

    A raw extraction has exactly one dot in its basename: the extension. Provision ids
    have their dots replaced with underscores by provision_filename, so `app_12_10.yaml`
    is safe and every compound suffix is excluded by construction rather than by name.
    """
    return os.path.basename(path).count(".") == 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default=OUT_GLOB)
    ap.add_argument("--report", action="store_true", help="Print the table only.")
    args = ap.parse_args()

    paths = sorted(p for p in glob.glob(args.glob) if is_raw_extraction(p))
    if not paths:
        print("No extraction output matched %s" % args.glob)
        return 1

    print("%-6s %-14s %-6s %-6s %-6s  %s"
          % ("prov", "modality", "norms", "conds", "excs", "raw->clean fields"))
    print("-" * 78)
    total_raw = total_kept = 0
    for path in paths:
        with io.open(path, encoding="utf-8") as fh:
            document = yaml.safe_load(fh)
        extracted = document.get("extracted_object") or {}

        raw_fields = kept_fields = 0
        for norm in (extracted.get("norms") or []):
            if not isinstance(norm, dict):
                continue
            for key in ("conditions", "exceptions"):
                for item in (norm.get(key) or []):
                    if isinstance(item, dict):
                        raw_fields += len(item)
        cleaned = clean_provision(extracted, _sentences(document))
        for norm in cleaned["norms"]:
            for key in ("conditions", "exceptions"):
                for item in norm.get(key, []):
                    kept_fields += len(item)
        total_raw += raw_fields
        total_kept += kept_fields

        conds = sum(len(n.get("conditions", [])) for n in cleaned["norms"])
        excs = sum(len(n.get("exceptions", [])) for n in cleaned["norms"])
        mods = ",".join(sorted({n.get("deontic_modality", "?") for n in cleaned["norms"]}))
        print("%-6s %-14s %-6d %-6d %-6d  %d -> %d"
              % (cleaned["provision_id"], mods[:14], len(cleaned["norms"]), conds, excs,
                 raw_fields, kept_fields))

        if not args.report:
            out_path = path.replace(".yaml", ".clean.yaml")
            with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
                yaml.dump(cleaned, fh, sort_keys=False, allow_unicode=True, width=100)

    print("-" * 78)
    print("condition/exception field values: %d raw, %d survived cleaning (%d discarded "
          "as placeholder or description echo)"
          % (total_raw, total_kept, total_raw - total_kept))
    if not args.report:
        print("wrote *.clean.yaml alongside each extraction")
    return 0


if __name__ == "__main__":
    sys.exit(main())
