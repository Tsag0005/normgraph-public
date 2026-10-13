"""Query strategy for the SPIRES norm graph.

A third graph shape, needing a third method. The APP graph is flat triples ranked by
proximity; the RP1 graph is small enough to serialise whole. This one is neither: it is
a reified norm structure where the useful unit is a Norm with its modality, bearer,
action, object, conditions and exceptions attached -- so retrieval walks *provisions*
and renders *norms*, and never emits a bare triple.

Two things here are specific to legislation rather than to graphs in general.

INBOUND EDGES MATTER AS MUCH AS OUTBOUND. Following SP_REFERS_TO forwards answers "what
does this clause depend on". Following it backwards answers "what defeats this clause",
which is the question a compliance test more often turns on: subclause 2.1 read alone
states an unqualified right to anonymity, and is actively misleading until 2.2 -- which
points AT it -- is also in view. Outbound-only retrieval would never find 2.2 from 2.1.

EXCEPTIONS ARE SURFACED BEFORE CONDITIONS. The measured failure of every arm in this
project is boundary coverage: six elements no configuration ever reached were all
negative or structural -- what a rule does NOT cover, what is exhaustive, what is
severable. Ordering exceptions first is a direct intervention on that.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import corpus_config as _corpus  # noqa: E402

from .config import Config
from .neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

# Labels and relationship types come from corpus.yaml, so several instruments can share
# one Aura instance under different prefixes. Cypher cannot parameterise either, so they
# are interpolated into the query strings below rather than passed as parameters.
L = _corpus.label
PROVISION_LABEL = L("Provision")

# How each modality reads in natural deontic language. APPLICABILITY is deliberately not
# rendered as a permission: APP 6.2 does not allow secondary use, it defines when 6.1's
# "unless" is satisfied, and an entity can meet 6.2 and still breach 6.1. Labelling it
# "may" would invite exactly that error in a generated test case.
MODALITY_PHRASE = {
    "OBLIGATION": "MUST",
    "PROHIBITION": "MUST NOT",
    "PERMISSION": "MAY",
    "DISAPPLICATION": "DISAPPLIES",
    "APPLICABILITY": "APPLIES-WHEN",
    "NONE": "NO DUTY OF ITS OWN",
}

DEONTIC = {"OBLIGATION", "PROHIBITION", "PERMISSION"}

_LEADING_CONNECTIVE = re.compile(r"^\s*(unless|except(?:\s+where|\s+that)?|other than)\s+", re.I)


def dedupe_exceptions(items):
    """Collapse carve-outs that differ only in their leading connective.

    The first extraction pass writes "the individual has consented"; the verification
    pass quotes the sentence verbatim and gets "unless the individual has consented".
    Both are the same exception, and showing the model both invites it to write two
    scenarios for one carve-out.
    """
    out, seen = [], set()
    for statement, source in items:
        folded = _LEADING_CONNECTIVE.sub("", statement or "").strip().lower().rstrip(".")
        if not folded or folded in seen:
            continue
        seen.add(folded)
        out.append((statement, source))
    return out


@dataclass
class Norm:
    key: str
    provision: str
    modality: str
    bearer: str = ""
    action: str = ""
    object: str = ""
    scope_qualifier: str = ""
    conditions: List[str] = field(default_factory=list)
    exceptions: List[Tuple[str, str]] = field(default_factory=list)  # (statement, source)


@dataclass
class SpiresContext:
    text: str = ""
    target_provision: Optional[str] = None
    norms_included: int = 0
    provisions_included: int = 0
    inbound_provisions: List[str] = field(default_factory=list)
    outbound_provisions: List[str] = field(default_factory=list)
    exceptions_included: int = 0
    token_estimate: int = 0

    def as_dict(self) -> dict:
        return {
            "target_provision": self.target_provision,
            "norms_included": self.norms_included,
            "provisions_included": self.provisions_included,
            "inbound_provisions": self.inbound_provisions,
            "outbound_provisions": self.outbound_provisions,
            "exceptions_included": self.exceptions_included,
            "token_estimate": self.token_estimate,
        }


_NORMS = """
MATCH (p:`{label}` {{provision: $pid}})-[:""" + L("HAS_NORM") + """]->(n:""" + L("Norm") + """)
OPTIONAL MATCH (n)-[:""" + L("BEARER") + """]->(b:""" + L("Actor") + """)
OPTIONAL MATCH (n)-[:""" + L("ACTION") + """]->(a:""" + L("Action") + """)
OPTIONAL MATCH (n)-[:""" + L("OBJECT") + """]->(o:""" + L("InfoType") + """)
RETURN n.key AS key, n.modality AS modality, n.scope_qualifier AS scope,
       b.name AS bearer, a.name AS action, o.name AS object
"""

# Conditions and exceptions are fetched separately rather than with more OPTIONAL MATCHes
# on the query above: collecting two independent one-to-many relationships in one pass
# multiplies them together, and the DISTINCT needed to undo that hides genuine duplicates.
_ATTACHED = """
MATCH (n:""" + L("Norm") + """)-[:`{rel}`]->(x)
WHERE n.key IN $keys
RETURN n.key AS key, x.statement AS statement,
       x.source_provision AS source_provision
"""

# Statutory text lives on :SP_Provision, put there by build_spires_graph.py. It used to
# be read out of the :AppNode graph, which made the EDC construction path a silent
# prerequisite for this one. Matched on `provision`, the bare id, rather than on `key`.
_PROVISION_TEXT = """
MATCH (t:`{label}`)
WHERE t.provision IN $pids AND t.text_raw IS NOT NULL AND t.text_raw <> ''
RETURN t.provision AS pid, t.name AS name, t.text_raw AS text_raw
"""

_NEIGHBOURS = """
MATCH (p:`{label}` {{provision: $pid}})-[r:""" + L("REFERS_TO") + """|""" + L("DISAPPLIES") + """]->(q:`{label}`)
RETURN q.provision AS pid, type(r) AS rel, 'outbound' AS direction
UNION
MATCH (p:`{label}` {{provision: $pid}})<-[r:""" + L("REFERS_TO") + """|""" + L("DISAPPLIES") + """]-(q:`{label}`)
RETURN q.provision AS pid, type(r) AS rel, 'inbound' AS direction
"""


def fetch_norms(client: Neo4jClient, pid: str) -> List[Norm]:
    rows = (client.norm_rows(pid) if hasattr(client, "norm_rows")
            else client.run(_NORMS.format(label=PROVISION_LABEL), pid=pid))
    norms = {}
    for row in rows:
        norms[row["key"]] = Norm(
            key=row["key"], provision=pid, modality=row.get("modality") or "",
            bearer=row.get("bearer") or "", action=row.get("action") or "",
            object=row.get("object") or "", scope_qualifier=row.get("scope") or "")
    if not norms:
        return []

    keys = list(norms)
    conditions = (client.attached_rows(keys, "CONDITION") if hasattr(client, "attached_rows")
                  else client.run(_ATTACHED.format(rel=L("CONDITION")), keys=keys))
    for row in conditions:
        if row.get("statement"):
            norms[row["key"]].conditions.append(row["statement"])
    exceptions = (client.attached_rows(keys, "EXCEPTION") if hasattr(client, "attached_rows")
                  else client.run(_ATTACHED.format(rel=L("EXCEPTION")), keys=keys))
    for row in exceptions:
        if row.get("statement"):
            norms[row["key"]].exceptions.append(
                (row["statement"], row.get("source_provision") or ""))
    for norm in norms.values():
        norm.exceptions = dedupe_exceptions(norm.exceptions)
    return [n for n in norms.values() if not is_empty(n)]


def walk(client: Neo4jClient, pid: str, depth: int, include_inbound: bool):
    """Provisions reachable from the target, with why and in which direction."""
    seen: Dict[str, dict] = {}
    frontier = {pid}
    for hop in range(1, depth + 1):
        next_frontier = set()
        for current in sorted(frontier):
            neighbours = (client.neighbours(current) if hasattr(client, "neighbours")
                          else client.run(_NEIGHBOURS.format(label=PROVISION_LABEL), pid=current))
            for row in neighbours:
                if row["direction"] == "inbound" and not include_inbound:
                    continue
                target = row["pid"]
                if target == pid or target in seen:
                    continue
                seen[target] = {"rel": row["rel"], "direction": row["direction"],
                                "hop": hop, "via": current}
                next_frontier.add(target)
        frontier = next_frontier
        if not frontier:
            break
    return seen


def is_empty(norm: Norm) -> bool:
    """A norm with no participants and nothing attached carries no information."""
    return not any((norm.bearer, norm.action, norm.object,
                    norm.conditions, norm.exceptions))


def render_norm(norm: Norm, exceptions_first: bool = True) -> List[str]:
    """One norm as an English sentence, not a pipe-delimited record.

    The earlier form was `MUST NOT | entity | use or disclose | the information`, which
    reads as a database row and asks the model to reassemble the grammar before it can
    use the rule. `entity MUST NOT use or disclose the information` says the same thing
    in the register the model then has to write in. The modality stays upper-case
    because it is the one part a paraphrase must not soften.
    """
    subject = norm.bearer or "the entity"
    predicate = " ".join(p for p in (norm.action, norm.object) if p)

    if norm.modality in ("OBLIGATION", "PROHIBITION", "PERMISSION"):
        # The modality works as an auxiliary verb, so the sentence carries it directly.
        head = "  %s %s %s" % (subject, MODALITY_PHRASE[norm.modality], predicate)
    elif norm.modality == "DISAPPLICATION":
        head = "  SWITCHES OFF the provision named below, where: %s %s" % (subject, predicate)
    elif norm.modality == "APPLICABILITY":
        # Not a permission. It states when an exception another provision relies on is
        # satisfied, and saying "may" here would licence conduct the clause never allows.
        head = "  APPLIES (satisfying an exception used by another provision) where: %s %s" % (
            subject, predicate)
    else:
        head = "  States no duty of its own: %s %s" % (subject, predicate)

    lines = ["  " + " ".join(head.split())]
    if norm.scope_qualifier:
        lines.append("      limited to: %s" % norm.scope_qualifier)

    def conditions():
        return ["      applies only where: %s" % c for c in norm.conditions]

    def exceptions():
        out = []
        for statement, source in norm.exceptions:
            suffix = "  [see %s]" % source if source else ""
            out.append("      EXCEPT where: %s%s" % (statement, suffix))
        return out

    lines += (exceptions() + conditions()) if exceptions_first else (conditions() + exceptions())
    return lines


def fetch_provision_text(client: Neo4jClient, pids: List[str]):
    """Verbatim text for the reached provisions, from the norm graph's own nodes.

    Previously read from the :AppNode graph via edc_bridge.provision_key, which made the
    EDC construction path a silent prerequisite for this arm: on a corpus where only the
    SPIRES graph had been built, every lookup returned nothing and the hybrid arm
    degraded to the plain one without saying so. build_spires_graph now writes text_raw
    onto the :SP_Provision nodes, so the arm reads the graph it is named after.
    """
    if not pids:
        return {}
    rows = (client.text_rows(pids) if hasattr(client, "text_rows")
            else client.run(_PROVISION_TEXT.format(label=PROVISION_LABEL), pids=list(pids)))
    out = {}
    for row in rows:
        text = (row.get("text_raw") or "").strip()
        if text:
            out[row["pid"]] = text
    return out


def serialise(target: str, target_norms: List[Norm],
              related: List[Tuple[str, dict, List[Norm]]],
              exceptions_first: bool, texts: Optional[Dict[str, str]] = None) -> str:
    lines = ["TARGET PROVISION — subclause %s" % target, ""]
    for norm in target_norms:
        lines += render_norm(norm, exceptions_first) + [""]

    if related:
        # Deliberately NOT labelled "background only, do not write scenarios for these".
        # That was tried: it drove on-target tagging from 39% to 100% and halved
        # cross-referenced element coverage, because scenarios about 6.2's limbs are how
        # elements 6.1-E13..E17 get covered at all. The connected provisions carry the
        # substance of the target's exceptions, so the generator SHOULD write about them.
        lines += ["PROVISIONS CONNECTED TO IT", ""]
        for pid, meta, norms in related:
            # Say why each provision is here. "2.2 disapplies this one" and "6.2 is
            # referred to by this one" call for opposite readings, and a bare list of
            # neighbouring clauses gives the model no way to tell them apart.
            if meta["direction"] == "inbound":
                why = ("%s DISAPPLIES the target" if meta["rel"] == L("DISAPPLIES")
                       else "%s refers to the target") % _corpus.provision_key(pid)
            else:
                why = ("the target DISAPPLIES subclause %s" if meta["rel"] == L("DISAPPLIES")
                       else "the target refers to subclause %s") % pid
            lines.append("%s — %s" % (_corpus.provision_key(pid), why))
            for norm in norms:
                lines += render_norm(norm, exceptions_first)
            lines.append("")

    if texts:
        # Kept as its own section rather than interleaved: the structure above is an
        # extraction and may be wrong, the text below is the statute and cannot be. The
        # model should be able to tell which is which at a glance.
        lines += ["VERBATIM TEXT OF THOSE PROVISIONS", ""]
        # Document order from the segmentation. The key here was
        # [int(x) for x in p.split(".")], which assumes every statute numbers clauses N.M
        # and raises ValueError on "26WH(2)" -- so this whole branch crashed on any
        # instrument that does not. It went unnoticed because it is off by default and
        # Schedule 1 happens to satisfy the assumption. The same key was removed from
        # spires/extract_corpus.py for the same reason.
        lines += _ordered_text_lines(texts)
    return "\n".join(lines).rstrip() + "\n"


def _ordered_text_lines(texts: Dict[str, str]) -> List[str]:
    """Provision texts in the order the instrument states them.

    Document order is already known and is always right; deriving one from the identifier
    grammar means parsing "26WE(1)" into something comparable, which is what the previous
    sort key tried and failed to do. Falls back to sorting by identifier if the
    segmentation cannot be read, so a context block is still produced.
    """
    try:
        import prep_corpus
        order = {pid: i for i, pid in enumerate(prep_corpus.order())}
    except Exception:  # noqa: BLE001 - ordering must never be why a run fails
        order = {}
    lines = []
    for pid in sorted(texts, key=lambda p: (order.get(p, len(order)), p)):
        lines += ["%s:" % _corpus.provision_key(pid), "  %s" % texts[pid], ""]
    return lines


def build_context(client: Neo4jClient, section_text: str, config: Config) -> SpiresContext:
    """Entry point: anchor on the target provision, walk its reference neighbourhood."""
    from .errors import RetrievalError
    from .tokens import estimate_tokens

    pid = config.provision_id or _corpus.detect_provision_id(section_text)
    if not pid:
        raise RetrievalError(
            "context_mode='spires' needs the target subclause id and none was found in "
            "the input. Pass --provision-id.")

    target_norms = fetch_norms(client, pid)
    if not target_norms:
        raise RetrievalError(
            "No SP_Norm attached to subclause %s. Is the SPIRES graph loaded? "
            "Run spires/build_spires_graph.py." % pid)

    neighbours = walk(client, pid, config.spires_hop_depth, config.spires_include_inbound)

    related = []
    for neighbour_pid, meta in sorted(neighbours.items(), key=lambda kv: (kv[1]["hop"], kv[0])):
        norms = fetch_norms(client, neighbour_pid)
        if config.spires_modality_filter == "deontic":
            # APPLICABILITY norms are the content of exceptions used elsewhere, so they
            # are kept even under a deontic filter -- dropping them would remove exactly
            # the 6.2 limbs the target's exceptions point at.
            norms = [n for n in norms
                     if n.modality in DEONTIC or n.modality == "APPLICABILITY"]
        if norms:
            related.append((neighbour_pid, meta, norms))

    texts = {}
    if config.spires_include_provision_text:
        texts = fetch_provision_text(client, [p for p, _m, _n in related])

    text = serialise(pid, target_norms, related, config.spires_exceptions_first, texts)
    tokens, _method = estimate_tokens(text, config.llm_model)

    inbound = sorted(k for k, v in neighbours.items() if v["direction"] == "inbound")
    outbound = sorted(k for k, v in neighbours.items() if v["direction"] == "outbound")
    total_norms = len(target_norms) + sum(len(n) for _p, _m, n in related)
    exceptions = sum(len(n.exceptions) for n in target_norms) + sum(
        len(x.exceptions) for _p, _m, norms in related for x in norms)

    logger.info("SPIRES context: subclause %s, %d norm(s) over %d provision(s) "
                "(%d inbound, %d outbound), %d exception(s), %d tokens",
                pid, total_norms, len(related) + 1, len(inbound), len(outbound),
                exceptions, tokens)

    return SpiresContext(
        text=text, target_provision=pid, norms_included=total_norms,
        provisions_included=len(related) + 1, inbound_provisions=inbound,
        outbound_provisions=outbound, exceptions_included=exceptions,
        token_estimate=tokens)
