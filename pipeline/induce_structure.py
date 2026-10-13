"""Stage 0: learn a statute's structure from the statute, so a new corpus is a .txt file.

The pipeline was coupled to Schedule 1 in seven places, and the worst of them was the
structural parser: `prep_app.py` knows that an Australian Privacy Principle is announced
by "N Australian Privacy Principle N-title" and that clauses are numbered "N.M". Nothing
about that transfers. Part IIIC numbers sections "26WE", nests subsections "(1)" inside
them, and marks divisions with an em dash.

Writing a parser per statute is the obvious answer and the wrong one. The extraction step
already spends an LLM call on every provision -- roughly $1.40 for a Part the size of
IIIC -- so spending ONE more call on the whole document to learn its shape costs about
$0.02, which is noise. That call replaces the parser.

A model asked for regexes will confidently return regexes that are wrong, so the call is
only half the design. The other half is `validate()`, which runs the induced profile over
the whole file and checks it mechanically:

    orphan rate       lines that reached no provision -- the signature of a missed pattern
    provision count   not 1, not one per line
    unique ids        a numbering scheme that collides is not the numbering scheme
    largest share     one provision holding a quarter of the text means under-segmentation
    empty provisions  an id with no words under it

None of those need a human or a second model. A profile that fails is retried once with
the failure report appended to the prompt; a profile that fails twice stops the run and
names the pattern that did not match, because an empty graph discovered three stages
later is the expensive outcome this is meant to prevent.

The same methodology as the soundness judge: do not trust the model, check it against
something that cannot lie.

    python induce_structure.py APPartIIIC.txt --dry-run      # prompt size and cost, no call
    python induce_structure.py APPartIIIC.txt --profile profiles/part_iiic.yaml
                                                             # validate a written profile, no call
    python induce_structure.py APPartIIIC.txt                # induce, validate, write the profile
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The profile model and the interpreter live with the code that runs on every build,
# not with this one-off tool. Importing them the other way round would make a routine
# corpus rebuild depend on a module that pulls in an OpenAI client.
from prep_corpus import (  # noqa: E402
    MIN_PROVISION_WORDS, ROLES, Hygiene, Level, Profile, Segmentation,
    compile_profile, hygiene, normalise, segment, unclaimed_heading_shapes)

try:
    import truststore  # noqa: F401  - routes TLS through the Windows cert store
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 - absent outside the proxied environment
    pass

ENV_LLM_KEY_PRIMARY = "OPENAI_API_KEY"
ENV_LLM_KEY_FALLBACK = "OPENAI_KEY"

# gpt-4o list price, USD per 1M tokens.
PRICE_IN, PRICE_OUT = 2.50, 10.00

# Legislative drafting has a closed vocabulary for citations. This is not per-statute and
# is not asked of the model -- it is only offered, so an instrument using a subset says so.
CITATION_NOUNS = ("section", "subsection", "paragraph", "subparagraph", "clause",
                  "subclause", "article", "regulation", "schedule", "division", "part",
                  "item", "rule")

# --- thresholds --------------------------------------------------------------------
# Calibrated against the hand-written Schedule 1 parser, whose output is known: 62
# provisions from 337 non-blank lines, no orphans. They are deliberately loose -- this is
# a smoke alarm for "the regexes did not match", not a quality score.
MAX_ORPHAN_RATE = 0.10
MIN_PROVISIONS = 5
MAX_LARGEST_SHARE = 0.25
MAX_PATTERN_HIT_RATE = 0.60      # a line class matching most of the file classifies nothing


# --- validation --------------------------------------------------------------------

@dataclass
class Validation:
    problems: List[str]
    metrics: Dict[str, object]
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def validate(raw_lines: List[str], profile: Profile) -> Validation:
    """Check an induced profile against the whole file. Free, and the point of the design."""
    problems = compile_profile(profile)
    if problems:
        return Validation(problems, {})

    non_blank = [normalise(l) for l in raw_lines if l.strip()]
    total_nb = len(non_blank)
    seg = segment(raw_lines, profile)

    # Both checks below measure what a level EFFECTIVELY claims, not what its pattern
    # matches in isolation. Segmentation is first-match-wins down an ordered list, so a
    # later level only ever sees the lines the levels above it did not take.
    #
    # Measuring patterns in isolation instead produced a false rejection worth recording:
    # an induced profile whose paragraph level was `^\((\w)\)\s+(.+)$` was rejected for
    # "matching 66% of lines", because `\w` matches a digit and so the pattern also
    # matches every "(1)" subsection opening. Ordered after the subsection level it in
    # fact claimed 46%, and the profile was correct -- it produced the same 106 provisions
    # as the hand-written one. The check was wrong, not the profile.
    for lv in profile.levels:
        hit = seg.hits.get(lv.name, 0)
        if total_nb and hit / float(total_nb) > MAX_PATTERN_HIT_RATE:
            problems.append(
                "level %r claims %.0f%% of non-blank lines -- it is not distinguishing "
                "anything: %s" % (lv.name, 100.0 * hit / total_nb, lv.pattern))
        elif hit == 0:
            problems.append(
                "level %r claims no lines -- either it matches nothing, or a level above "
                "it already takes everything it would match: %s" % (lv.name, lv.pattern))

    n = len(seg.provisions)
    orphan_rate = seg.interior_orphans / float(seg.non_blank or 1)
    words = {k: len(v.split()) for k, v in seg.provisions.items()}
    total_words = sum(words.values()) or 1
    largest = max(words.values()) if words else 0
    largest_share = largest / float(total_words)
    thin = sorted(k for k, w in words.items() if w < profile.min_provision_words)

    metrics = {
        "provisions": n,
        "non_blank_lines": seg.non_blank,
        "leading_orphans": seg.leading_orphans,
        "interior_orphans": seg.interior_orphans,
        "orphan_rate": round(orphan_rate, 4),
        "unmatched_lines": seg.unmatched_lines,
        "level_hits": seg.hits,
        "discarded_container_blocks": seg.discarded,
        "largest_provision_share": round(largest_share, 4),
        "thin_provisions": thin[:10],
        "first_ids": seg.order[:8],
        "last_ids": seg.order[-4:],
    }

    if n < MIN_PROVISIONS:
        problems.append(
            "only %d provision(s) found in %d non-blank lines -- the provision pattern "
            "%r did not match. This is what an empty graph looks like three stages early."
            % (n, seg.non_blank, (profile.provision_level or Level("?", "?", "?")).pattern))
    if n > seg.non_blank / 2.0:
        problems.append(
            "%d provisions from %d non-blank lines -- the provision pattern is matching "
            "body text, not clause openings" % (n, seg.non_blank))
    if orphan_rate > MAX_ORPHAN_RATE:
        problems.append(
            "%.1f%% of non-blank lines (%d) sit after a provision has been seen but "
            "belong to none, above the %.0f%% ceiling -- a structural form is "
            "unrecognised mid-instrument and its text is being dropped"
            % (100 * orphan_rate, seg.interior_orphans, 100 * MAX_ORPHAN_RATE))
    if largest_share > MAX_LARGEST_SHARE:
        problems.append(
            "one provision holds %.0f%% of all text -- under-segmentation, usually a "
            "provision pattern that matches the first clause form and not the rest"
            % (100 * largest_share))
    if thin:
        problems.append(
            "%d provision(s) have fewer than %d words, e.g. %s -- these are sub-headings "
            "being read as rules" % (len(thin), profile.min_provision_words, ", ".join(thin[:5])))

    duplicate = len(seg.order) != len(set(seg.order))
    if duplicate:
        problems.append("provision identifiers are not unique -- the id template %r does "
                        "not capture enough of the numbering scheme" % profile.id_template)

    # The identifier pattern is a different job from the level patterns: those classify
    # whole lines, this one has to find a citation mid-sentence. It is checked against the
    # identifiers segmentation actually produced, which is the only ground truth available
    # -- a pattern that cannot recognise its own corpus's ids will silently fail to detect
    # which provision an input section is about.
    if profile.identifier_pattern:
        try:
            identifier_re = re.compile(profile.identifier_pattern, re.IGNORECASE)
        except re.error as exc:
            problems.append("identifier_pattern does not compile (%s): %s"
                            % (exc, profile.identifier_pattern))
        else:
            missed = []
            for pid in seg.order:
                match = identifier_re.search(pid)
                if match is None or match.group(1) != pid:
                    missed.append(pid)
            if missed:
                problems.append(
                    "identifier_pattern %r does not recover %d of %d identifiers, e.g. %s"
                    % (profile.identifier_pattern, len(missed), len(seg.order),
                       ", ".join(missed[:5])))
            metrics["identifier_pattern_ok"] = not missed
    else:
        problems.append(
            "no identifier_pattern -- without one, nothing can find a provision id inside "
            "running text, and corpus.yaml cannot be generated from this profile")

    warnings = []
    for word, count, example in unclaimed_heading_shapes(raw_lines, profile):
        warnings.append(
            "%d line(s) begin %r and match no level, e.g. %r -- if that is a heading "
            "form, it is being absorbed into the provision above it"
            % (count, word, example[:70]))

    return Validation(problems, metrics, warnings)


# --- induction ---------------------------------------------------------------------

def sample_for_prompt(raw_lines: List[str], budget_lines: int = 160) -> str:
    """Head, middle and tail. The head alone would miss forms a Part introduces later."""
    non_blank = [normalise(l) for l in raw_lines if l.strip()]
    if len(non_blank) <= budget_lines:
        return "\n".join(non_blank)
    third = budget_lines // 3
    mid = len(non_blank) // 2
    parts = (non_blank[:third],
             non_blank[mid - third // 2: mid + third // 2],
             non_blank[-third:])
    return "\n\n[...]\n\n".join("\n".join(p) for p in parts)


PROMPT = """You are given a sample of an Act. Describe its LINE STRUCTURE so that a
program can split the whole file into provisions.

Return JSON only, with this exact shape:

{{
  "name": "short_snake_case_identifier_for_this_instrument",
  "description": "one line, e.g. Part IIIC of the Privacy Act 1988 (Cth)",
  "levels": [
    {{"name": "section", "role": "container", "pattern": "^(\\\\d{{2}}[A-Z]{{1,3}})\\\\s\\\\s+(.+)$"}}
  ],
  "id_template": "{{section}}({{subsection}})",
  "identifier_pattern": "\\\\b(\\\\d{{2}}[A-Z]{{1,3}}(?:\\\\(\\\\d+[A-Z]?\\\\))?)",
  "citation_nouns": ["section", "subsection"],
  "note_pattern": "^Note(\\\\s+\\\\d+)?:",
  "min_provision_words": 8
}}

Rules, all of which matter:

* `levels` is ORDERED, outermost first, and the first pattern that matches a line wins.
  Put narrower patterns before wider ones. "(a)" and "(i)" overlap: bound them so that
  roman numerals and letters do not both match the same line.
* Every pattern is a Python regex, anchored with ^, matching ONE line, with the
  IDENTIFIER as capture group 1. No nested quantifiers such as (\\\\w+)+.
* `role` is one of:
    heading    structural signpost belonging to no provision (a Part or Division title)
    container  opens an identifier namespace and may stand alone as a provision if
               nothing below it opens (a section that is a single sentence)
    provision  THE unit the knowledge graph is keyed on -- exactly one level has this
    body       a continuation of whatever provision is open (lettered paragraphs)
* Choose the provision level so that each provision is one self-contained rule. If
  sections carry numbered subsections, the subsection is the provision, not the section.
* `id_template` composes the identifier from the captures of the enclosing levels, using
  their level names, e.g. "{{section}}({{subsection}})" or "{{clause}}".
* `identifier_pattern` is a DIFFERENT job from the level patterns. Those classify whole
  lines; this one finds an identifier inside running text, as it appears in a citation
  like "see subsection 26WR(2)". It is NOT anchored, and capture group 1 must be exactly
  the identifier that `id_template` would compose -- it is checked against every
  identifier the file actually yields, so a pattern that cannot recover its own corpus's
  ids is rejected. Make optional any part that a bare citation omits.
* `citation_nouns` are the words this instrument uses to cite itself, drawn from:
  {nouns}. Order them so the one used most often for a provision of this instrument comes
  FIRST -- it becomes the prefix identifiers are stored under.

Do not describe the meaning of the text. Describe only how lines are laid out.

SAMPLE
------
{sample}
"""

RETRY_SUFFIX = """

Your previous answer was applied to the whole file and rejected. Fix these and return
corrected JSON only:

{problems}

Measurements from the attempt:
{metrics}
"""


def build_prompt(raw_lines: List[str], problems=None, metrics=None) -> str:
    prompt = PROMPT.format(nouns=", ".join(CITATION_NOUNS),
                           sample=sample_for_prompt(raw_lines))
    if problems:
        prompt += RETRY_SUFFIX.format(
            problems="\n".join("  - " + p for p in problems),
            metrics=json.dumps(metrics, indent=2, default=str))
    return prompt


# --- generating corpus.yaml ---------------------------------------------------------

def derive_key_prefix(raw_lines: List[str], profile: Profile) -> str:
    """Which citation noun this instrument uses when naming one of its own provisions.

    `subclause 6.1` for Schedule 1, `section 26WE` for Part IIIC. It becomes the prefix
    provision keys are stored under, so guessing it wrong means every lookup misses.

    The model is asked to order `citation_nouns` by frequency, but that is a claim about
    the corpus and claims about the corpus can be counted. This counts: for each noun, how
    often it appears immediately before something the identifier pattern recognises. The
    model's ordering is the tie-break, not the answer.
    """
    if not profile.identifier_pattern or not profile.citation_nouns:
        return (profile.citation_nouns or [""])[0]
    text = " ".join(normalise(l) for l in raw_lines if l.strip())
    counts = {}
    for noun in profile.citation_nouns:
        pattern = re.compile(r"\b%s\s+(?=%s)" % (re.escape(noun), profile.identifier_pattern),
                             re.IGNORECASE)
        counts[noun] = len(pattern.findall(text))
    best = max(profile.citation_nouns, key=lambda n: (counts[n], -profile.citation_nouns.index(n)))
    return best if counts[best] else profile.citation_nouns[0]


def corpus_config_dict(profile: Profile, raw_lines: List[str], raw_path: str,
                       profile_path: str) -> dict:
    """The corpus.yaml a profile implies.

    Everything here used to be hand-written, and every value in it is derivable from the
    profile plus the corpus name -- which is the point: a new statute should be a .txt
    file and a generated config, not a config someone had to reason their way to.

    node_prefix is the one value that cannot be checked here. Several corpora share one
    Aura instance, so two of them claiming the same prefix would silently merge their
    graphs; that check needs the registry, and lives with the loader.
    """
    name = profile.name
    slug = re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_")
    short = "".join(part[0] for part in slug.split("_") if part)[:4] or "c"
    return {
        "name": name,
        "description": profile.description,
        "file_prefix": slug.split("_")[0][:12] or "corpus",
        "node_prefix": "%s_" % short.upper(),
        "paths": {
            "raw": raw_path.replace(os.sep, "/"),
            "spires_dir": "spires",
            "profile": profile_path.replace(os.sep, "/"),
        },
        "modality_overrides": {},
        "prompt": _prompt_strings(profile, raw_lines),
        "provision": {
            "pattern": profile.identifier_pattern,
            "key_prefix": derive_key_prefix(raw_lines, profile),
        },
    }


def _prompt_strings(profile: Profile, raw_lines: List[str]) -> dict:
    """Examples block 1 of the prompt uses to teach "a policy will never cite this".

    Naming a provision is the only way to teach that rule, so these are corpus-specific by
    nature. Generated from the first real provision this instrument has, which is a
    starting point rather than an answer: the point of putting them in corpus.yaml is that
    a human can improve them without touching the prompt.
    """
    noun = derive_key_prefix(raw_lines, profile)
    try:
        first = segment(raw_lines, profile).order[0]
    except Exception:  # noqa: BLE001 - a profile that cannot segment is rejected elsewhere
        first = ""
    example = ("%s %s" % (noun, first)).strip()
    return {
        "citation_examples": '"%s", "%s"' % (example, profile.description or "the Act"),
        "example_provision": example,
        "provision_plural": "%ss" % noun if noun else "provision identifiers",
    }


def write_corpus_config(path: str, config: dict, source: str) -> None:
    """Write a generated corpus.yaml, refusing to clobber a hand-edited one."""
    import yaml
    header = [
        "# Generated by induce_structure.py from the structural profile -- see `profile:`.",
        "#",
        "# Source: %s" % source,
        "#",
        "# Hand-editable. Two values are worth checking before a build:",
        "#   node_prefix   several corpora share one Aura instance, and two claiming the",
        "#                 same prefix would merge their graphs without complaint.",
        "#   key_prefix    counted from the corpus (how often each citation noun precedes",
        "#                 an identifier), not taken on the model's word.",
        "",
    ]
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(header))
        yaml.safe_dump(config, fh, sort_keys=False, allow_unicode=True,
                       default_flow_style=False)


def llm_key_from_env() -> str:
    key = os.environ.get(ENV_LLM_KEY_PRIMARY) or os.environ.get(ENV_LLM_KEY_FALLBACK)
    if not key:
        raise RuntimeError("Set %s (or %s). Run `. .\\setup_env.ps1` first."
                           % (ENV_LLM_KEY_PRIMARY, ENV_LLM_KEY_FALLBACK))
    return key


def induce(prompt: str, model: str) -> tuple:
    """One call. Returns (profile_dict, prompt_tokens, completion_tokens)."""
    from openai import OpenAI
    client = OpenAI(api_key=llm_key_from_env())
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    usage = response.usage
    return (json.loads(response.choices[0].message.content),
            usage.prompt_tokens, usage.completion_tokens)


# --- output ------------------------------------------------------------------------

def write_profile(path: str, profile: Profile, metrics: dict, source: str) -> None:
    """Write the profile as YAML, with the evidence for it in comments.

    Hand-editable on purpose. An induction that gets one pattern slightly wrong should be
    correctable here rather than by re-running the call or patching code.
    """
    try:
        import yaml
    except ImportError:
        with io.open(path.replace(".yaml", ".json"), "w", encoding="utf-8") as fh:
            json.dump(profile.to_dict(), fh, indent=2)
        return

    header = [
        "# Structural profile induced by induce_structure.py -- NOT hand-written.",
        "#",
        "# Source: %s" % source,
        "# Validated against the whole file before being written:",
    ]
    for key in ("provisions", "non_blank_lines", "leading_orphans", "interior_orphans",
                "largest_provision_share", "discarded_container_blocks"):
        header.append("#   %-24s %s" % (key, metrics.get(key)))
    header += [
        "#   %-24s %s" % ("first ids", ", ".join(map(str, metrics.get("first_ids", [])))),
        "#",
        "# Correct it by hand if a pattern is slightly wrong -- that is cheaper than",
        "# re-inducing, and this file is what the pipeline actually reads.",
        "",
    ]
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(header))
        yaml.safe_dump(profile.to_dict(), fh, sort_keys=False, allow_unicode=True,
                       default_flow_style=False)


def report(hy: Hygiene, val: Validation, profile: Optional[Profile]) -> None:
    print("input")
    print("  lines            : %d (%d non-blank)" % (hy.lines, hy.non_blank))
    print("  mean line length : %.1f words" % hy.mean_words)
    for w in hy.warnings:
        print("  WARNING: %s" % w)

    if profile:
        print("\nprofile %r" % profile.name)
        for lv in profile.levels:
            print("  %-14s %-10s %s" % (lv.name, lv.role, lv.pattern))
        print("  %-14s %-10s %s" % ("id template", "", profile.id_template))

    if val.metrics:
        m = val.metrics
        print("\nvalidation")
        print("  provisions       : %s" % m["provisions"])
        print("  orphans          : %s leading (front matter, expected), %s interior (%.1f%%)"
              % (m["leading_orphans"], m["interior_orphans"], 100 * m["orphan_rate"]))
        print("  unmatched lines  : %s" % m["unmatched_lines"])
        print("  largest share    : %.1f%%" % (100 * m["largest_provision_share"]))
        print("  discarded blocks : %s" % m["discarded_container_blocks"])
        print("  level hits       : %s" % m["level_hits"])
        print("  first ids        : %s" % ", ".join(map(str, m["first_ids"])))
        print("  last ids         : %s" % ", ".join(map(str, m["last_ids"])))

    for w in val.warnings:
        print("  NOTE: %s" % w)

    if val.problems:
        print("\nREJECTED")
        for p in val.problems:
            print("  - %s" % p)
    elif profile:
        print("\nACCEPTED")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Induce and validate a statute's line structure from the text itself.")
    ap.add_argument("corpus", help="Plain-text statute, one line per structural element.")
    ap.add_argument("--profile", default=None,
                    help="Validate this existing profile instead of calling the model. "
                         "Free, and the way to check the format by hand.")
    ap.add_argument("--out", default=None,
                    help="Where to write the accepted profile (default: profiles/<name>.yaml).")
    ap.add_argument("--model", default="gpt-4o")
    ap.add_argument("--retries", type=int, default=1,
                    help="Re-ask once with the failure report appended.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the prompt size and cost estimate, make no call.")
    ap.add_argument("--write-config", nargs="?", const="corpus.yaml", default=None,
                    metavar="PATH",
                    help="Also generate the corpus.yaml this profile implies. Refuses to "
                         "overwrite an existing file: regenerating a live config would "
                         "change node_prefix and orphan the graph already loaded under "
                         "the old one.")
    ap.add_argument("--force", action="store_true",
                    help="Allow --write-config to overwrite.")
    args = ap.parse_args()

    if not os.path.exists(args.corpus):
        print("No such file: %s" % args.corpus, file=sys.stderr)
        return 2
    raw_lines = io.open(args.corpus, encoding="utf-8").read().splitlines()
    hy = hygiene(raw_lines)

    def emit_config(profile, profile_path):
        """Write the generated corpus.yaml, if asked and if it is safe to."""
        if not args.write_config:
            return
        target = args.write_config
        if os.path.exists(target) and not args.force:
            print("\nNOT writing %s: it already exists. Regenerating a live config would "
                  "change node_prefix and orphan the graph loaded under the old one. Pass "
                  "--force, or --write-config with another path." % target, file=sys.stderr)
            return
        config = corpus_config_dict(profile, raw_lines, args.corpus, profile_path)
        write_corpus_config(target, config, args.corpus)
        print("\nwrote %s" % target)
        print("  file prefix : %s_*" % config["file_prefix"])
        print("  node prefix : %s  <- check this is not already in use" % config["node_prefix"])
        print("  key prefix  : %s  <- counted from the corpus"
              % config["provision"]["key_prefix"])

    # --- offline path: validate a profile someone wrote --------------------------
    if args.profile:
        import yaml
        with io.open(args.profile, encoding="utf-8") as fh:
            profile = Profile.from_dict(yaml.safe_load(fh))
        val = validate(raw_lines, profile)
        report(hy, val, profile)
        if val.ok:
            emit_config(profile, args.profile)
        return 0 if val.ok else 1

    # --- dry run ------------------------------------------------------------------
    prompt = build_prompt(raw_lines)
    approx_in = len(prompt) // 4
    if args.dry_run:
        report(hy, Validation([], {}), None)
        cost = (approx_in * PRICE_IN + 900 * PRICE_OUT) / 1e6
        print("\ndry run")
        print("  sample lines     : %d of %d non-blank" % (
            len(sample_for_prompt(raw_lines).splitlines()), hy.non_blank))
        print("  prompt           : ~%d tokens" % approx_in)
        print("  calls            : 1 (+%d retry on rejection)" % args.retries)
        print("  cost             : ~$%.4f on %s, ~$%.4f worst case with retries"
              % (cost, args.model, cost * (1 + args.retries)))
        return 0

    # --- induce, validate, retry once ----------------------------------------------
    problems, metrics, spent_in, spent_out = None, None, 0, 0
    for attempt in range(args.retries + 1):
        prompt = build_prompt(raw_lines, problems, metrics)
        print("attempt %d: asking %s for a structural profile..." % (attempt + 1, args.model))
        raw, p_tok, c_tok = induce(prompt, args.model)
        spent_in, spent_out = spent_in + p_tok, spent_out + c_tok
        profile = Profile.from_dict(raw)
        val = validate(raw_lines, profile)
        report(hy, val, profile)
        if val.ok:
            out = args.out or os.path.join("profiles", "%s.yaml" % profile.name)
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            write_profile(out, profile, val.metrics, args.corpus)
            print("\nwrote %s" % out)
            emit_config(profile, out)
            print("cost: $%.4f (%d in, %d out)"
                  % ((spent_in * PRICE_IN + spent_out * PRICE_OUT) / 1e6, spent_in, spent_out))
            return 0
        problems, metrics = val.problems, val.metrics

    print("\nGiving up after %d attempt(s). The patterns above did not fit the file."
          % (args.retries + 1), file=sys.stderr)
    print("Write the profile by hand and check it with --profile; that is the escape "
          "hatch, and it is meant to be used rather than worked around.", file=sys.stderr)
    print("cost: $%.4f" % ((spent_in * PRICE_IN + spent_out * PRICE_OUT) / 1e6), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
