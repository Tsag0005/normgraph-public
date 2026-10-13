"""Find the cross-references a provision makes, driven by the structural profile.

Cross-referenced coverage is the only tier where the graph measurably beat the no-context
control, so the edges this produces are the graph's reason to exist. `enrich_graph`'s
version is three hand-written patterns and a range bound of `hi <= 13`, which is the
number of Australian Privacy Principles.

Measured on Part IIIC, 134 citations classified disjointly:

    relative internal                        56   42%   no mechanism existed at all
    absolute, inside Part IIIC               36   27%
    external instrument                      22   16%   dropped silently
    absolute, elsewhere in the Privacy Act   20   15%   dropped silently

Schedule 1, for contrast: 33 citations, 2 relative, none unresolvable. So nothing here
was exercised by the corpus the patterns were written against.

THE RELATIVE FORM IS DERIVED, NOT DECLARED. A relative citation -- "subsection (1)",
"paragraph (2)(a)" -- exists only when identifiers are composed from more than one level.
`id_template` says how they are composed, so the shape of a relative citation follows from
it: `{section}({subsection})` means a bare `(N)` is a citation, while Schedule 1's
`{clause}` has nothing to leave off and therefore has no relative form. That is why one
corpus has 56 of them and the other has 2.

RESOLUTION IS AGAINST THE CONTAINING PROVISION. "subsection (1)" inside 26WE(3) means
26WE(1). Of Part IIIC's 56 relative citations, 50 resolve this way; the other six are
letter-first -- "paragraph (a)", "paragraph (h)" -- which point inside the provision
making them and correctly produce no edge.

The regex is not redundant with SPIRES extraction, which was checked rather than assumed:
over the 62 Schedule 1 clauses the regex finds cross-references in 26 provisions against
SPIRES' 10, with exactly one provision that only SPIRES found. Taking edges from the
extraction would discard most of the graph's connectivity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

# Legislative drafting cites itself with a closed vocabulary. This is not per-statute;
# the profile says which subset an instrument uses, and the plural is added here.
_PLURALISE = "s?"

# A citation followed by this is pointing at another instrument, not at this one. Kept
# tight deliberately: an earlier throwaway version looked 60 characters ahead and read
# "as soon as practicable after the commencement" as an external reference.
_EXTERNAL_TAIL = re.compile(
    r"\s*(?:\([a-z0-9]{1,4}\)\s*)*of\s+(?:the|that)\s+([A-Z][\w'’-]*(?:\s+[\w'’-]+){0,6}"
    r"\s+Act\s+\d{4}|Constitution|Act)", re.IGNORECASE)

# "Australian Privacy Principle 8.1" -- a named instrument rather than a citation noun.
# Two or more capitalised words followed by something identifier-shaped.
_NAMED_INSTRUMENT = re.compile(
    r"\b((?:[A-Z][a-z]+\s+){1,5}[A-Z][a-z]+)s?\s+(\d+(?:\.\d+)?)\b")


def _singular(text: str) -> str:
    """`Principles` -> `Principle`. Enough to stop one instrument getting two stubs.

    Deliberately crude: legislative instrument names pluralise regularly, and anything
    cleverer would need a word list that is itself a per-corpus artefact.
    """
    text = text.strip()
    return text[:-1] if len(text) > 3 and text.endswith("s") and not text.endswith("ss") else text


@dataclass
class Citation:
    """One reference found in a provision's text."""
    raw: str                       # the matched substring, for the stub node's label
    kind: str                      # internal | external | self | unresolved
    target: Optional[str] = None   # provision id, when internal
    instrument: Optional[str] = None   # named Act, when external

    def as_dict(self) -> dict:
        return {"raw": self.raw, "kind": self.kind,
                "target": self.target, "instrument": self.instrument}


@dataclass
class Component:
    """One piece of a composed identifier, with the literals around it."""
    name: str
    prefix: str
    suffix: str


def parse_template(template: str) -> List[Component]:
    """`{section}({subsection})` -> [section '' '', subsection '(' ')'].

    The literals matter as much as the names: they are what a relative citation is
    wrapped in, and what separates the leading component from the rest of an identifier.
    """
    out: List[Component] = []
    pending = ""
    for piece in re.split(r"(\{[^}]*\})", template):
        if not piece:
            continue
        if piece.startswith("{") and piece.endswith("}"):
            out.append(Component(piece[1:-1], pending, ""))
            pending = ""
        else:
            # A literal between two components introduces the NEXT one; only a literal
            # after the last component is a suffix. Attaching it to the previous
            # component instead leaves components[1].prefix empty, so leading_component
            # never splits an identifier and nothing resolves -- which is precisely the
            # failure this comment exists to prevent recurring.
            pending += piece
    if pending and out:
        out[-1].suffix = pending
    return out


def leading_component(identifier: str, components: Sequence[Component]) -> str:
    """`26WE(3)` -> `26WE`. The part a relative citation is resolved against.

    Uses the literal that introduces the second component, so it follows the template
    rather than assuming parentheses.
    """
    if len(components) < 2:
        return identifier
    marker = components[1].prefix
    if marker and marker in identifier:
        return identifier.split(marker, 1)[0]
    return identifier


def relative_forms(known: Sequence[str], components: Sequence[Component]) -> List[str]:
    """The literal tails a relative citation can take, longest first.

    Built from the identifiers segmentation actually produced rather than from a regex
    over the template, so it cannot disagree with the corpus. Schedule 1's single-component
    identifiers yield none, which is the correct answer: it has no relative form.
    """
    if len(components) < 2:
        return []
    tails = set()
    for identifier in known:
        head = leading_component(identifier, components)
        tail = identifier[len(head):]
        if tail:
            tails.add(tail)
    return sorted(tails, key=len, reverse=True)


class ReferenceParser:
    """Compiled citation patterns for one instrument."""

    def __init__(self, profile, known: Sequence[str]):
        self.profile = profile
        self.known: Set[str] = set(known)
        self.components = parse_template(profile.id_template or "")
        self.sections = {leading_component(k, self.components) for k in self.known}

        nouns = [n for n in (profile.citation_nouns or []) if n.strip()]
        noun_alt = "|".join(re.escape(n) for n in sorted(nouns, key=len, reverse=True))
        self._noun = noun_alt or r"(?!x)x"       # matches nothing when none are declared

        identifier = profile.identifier_pattern or r"(?!x)x"
        # A run: "26WK and 26WL", "6.2 or 6.3", "5 to 13". The conjunction carries the
        # noun across, so a naive single-identifier pattern silently drops the second.
        self.absolute = re.compile(
            r"\b(?:%s)%s\s+(%s(?:\s*(?:,|and|or|to)\s*%s)*)"
            % (self._noun, _PLURALISE, identifier, identifier), re.IGNORECASE)
        self.identifier = re.compile(identifier, re.IGNORECASE)

        tails = relative_forms(self.known, self.components)
        if tails:
            tail_alt = "|".join(re.escape(t) for t in tails)
            self.relative = re.compile(
                r"\b(?:%s)%s\s+((?:%s))" % (self._noun, _PLURALISE, tail_alt),
                re.IGNORECASE)
        else:
            self.relative = None

    # --- helpers ------------------------------------------------------------------

    def _expand(self, run: str) -> List[str]:
        """Every identifier a run names.

        `enrich_graph._expand` bounded ranges with `hi <= 13`, the number of Australian
        Privacy Principles. The bound here is the corpus: a range expands only to
        identifiers segmentation produced, so it needs no magic number and cannot invent
        provisions that do not exist.
        """
        found = [m.group(1) for m in self.identifier.finditer(run)]
        if not re.search(r"\bto\b", run, re.IGNORECASE) or len(found) < 2:
            return found
        order = [k for k in self.known]
        try:
            lo, hi = order.index(found[0]), order.index(found[-1])
        except ValueError:
            return found
        if lo < hi:
            return order[lo:hi + 1]
        return found

    def _external_at(self, text: str, end: int):
        match = _EXTERNAL_TAIL.match(text, end)
        return _singular(match.group(1)) if match else None

    def _names_a_level(self, name: str) -> bool:
        """Does this phrase name one of THIS instrument's own structural levels?"""
        words = {_singular(w.lower()) for w in name.split()}
        return any(_singular(level.name.replace("_", " ").lower()) in words
                   for level in self.profile.levels)

    # --- the entry point ------------------------------------------------------------

    def parse(self, text: str, self_id: str) -> List[Citation]:
        """Every citation in one provision's text, classified and resolved."""
        out: List[Citation] = []
        seen = set()

        def add(citation):
            key = (citation.kind, citation.target, citation.instrument, citation.raw)
            if key not in seen:
                seen.add(key)
                out.append(citation)

        # Absolute, including runs.
        for match in self.absolute.finditer(text):
            instrument = self._external_at(text, match.end())
            if instrument:
                add(Citation(raw=match.group(0), kind="external", instrument=instrument))
                continue
            for identifier in self._expand(match.group(1)):
                if identifier == self_id:
                    add(Citation(raw=identifier, kind="self", target=identifier))
                elif identifier in self.known:
                    add(Citation(raw=identifier, kind="internal", target=identifier))
                elif identifier in self.sections:
                    # A section cited whole, whose provisions are its subsections.
                    for candidate in sorted(self.known):
                        if leading_component(candidate, self.components) == identifier:
                            add(Citation(raw=identifier, kind="internal", target=candidate))
                else:
                    add(Citation(raw=identifier, kind="unresolved"))

        # Relative, resolved against the provision making the citation.
        if self.relative is not None:
            head = leading_component(self_id, self.components)
            for match in self.relative.finditer(text):
                target = head + match.group(1)
                if target == self_id:
                    add(Citation(raw=match.group(1), kind="self", target=target))
                elif target in self.known:
                    add(Citation(raw=match.group(1), kind="internal", target=target))
                else:
                    add(Citation(raw=match.group(1), kind="unresolved"))

        # Named instruments: "Australian Privacy Principle 8.1".
        for match in _NAMED_INSTRUMENT.finditer(text):
            name = match.group(1)
            if name.lower().startswith(("this ", "the ")):
                continue
            if self._names_a_level(name):
                # An instrument does not cite itself by name. Schedule 1 has a level it
                # calls a `principle`, so "Australian Privacy Principle 6" is internal to
                # it -- treating that as external would have manufactured a stub for
                # something already in the graph. Part IIIC has no such level, so the same
                # phrase there is a genuine reference out to Schedule 1.
                continue
            add(Citation(raw=match.group(0), kind="external",
                         instrument=_singular(name)))

        return out


def targets(citations: Sequence[Citation]) -> List[str]:
    """Just the internal provision ids, deduplicated and ordered."""
    seen, out = set(), []
    for citation in citations:
        if citation.kind == "internal" and citation.target not in seen:
            seen.add(citation.target)
            out.append(citation.target)
    return out


def summarise(per_provision: Dict[str, List[Citation]]) -> dict:
    """Counts by class, plus the resolution rate the validation gate reads.

    Externals are excluded from the denominator: a statute that points at other Acts is
    not a statute whose parser is broken. What matters is the share of citations that look
    internal and nonetheless fail to resolve.
    """
    counts = {"internal": 0, "external": 0, "self": 0, "unresolved": 0}
    for citations in per_provision.values():
        for citation in citations:
            counts[citation.kind] = counts.get(citation.kind, 0) + 1
    internal_like = counts["internal"] + counts["unresolved"]
    counts["internal_resolution_rate"] = (
        1.0 if not internal_like else counts["internal"] / float(internal_like))
    counts["provisions_with_references"] = sum(
        1 for cs in per_provision.values() if any(c.kind == "internal" for c in cs))
    return counts
