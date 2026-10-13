"""Turn the SPIRES extractions into a reified norm graph in the existing Aura instance.

The analogue of build_graph.py, but the shape is the whole point. EDC's graph is flat
binary triples -- `APP entity - holds - personal information` -- and the controlled
experiment measured those as net-negative: withholding them beat including them. A Norm
is not a triple, so it is reified as a node with typed edges to its bearer, action,
object, conditions and exceptions. Flattening here would re-create exactly the failure
the extraction was bought to avoid.

    :SP_Provision {key:'6.1'} -[:SP_HAS_NORM]-> :SP_Norm {modality:'PROHIBITION'}
        -[:SP_BEARER]->    :SP_Actor      {name:'APP entity'}
        -[:SP_ACTION]->    :SP_Action     {name:'use or disclose'}
        -[:SP_OBJECT]->    :SP_InfoType   {name:'personal information'}
        -[:SP_CONDITION]-> :SP_Condition  {statement:'for a secondary purpose'}
        -[:SP_EXCEPTION]-> :SP_Exemption  {statement:'unless the individual has consented'}
    :SP_Provision {key:'2.2'} -[:SP_DISAPPLIES]-> :SP_Provision {key:'2.1'}
    :SP_Provision {key:'6.1'} -[:SP_REFERS_TO]->  :SP_Provision {key:'6.2'}

Two sources, each doing what it measured best at:

    norms, modality, conditions, exceptions   SPIRES
    cross-reference edges                     references.ReferenceParser

That split is not a preference. Over all 62 clauses the regex finds cross-references in
26 provisions against SPIRES' 10, with exactly one provision that only SPIRES found -- so
taking edges from SPIRES would discard most of the graph's connectivity.

Citations that leave the corpus become :SP_ExternalRef stubs. They used to be dropped
with a count printed, which left the graph understating its own incompleteness.

Actors, actions and information types are shared across provisions; conditions and
exceptions are not. A condition is a sentence about one norm, so pooling identical
statements would assert a connection between provisions that the statute does not make.
Shared vocabulary nodes are what give the graph cross-provision structure at all.

    python spires/build_spires_graph.py --dry-run
    python spires/build_spires_graph.py
    python spires/build_spires_graph.py --wipe        # drop SP_* first
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import io
import os
import re
import sys
import urllib.parse

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import corpus_config  # noqa: E402
import prep_corpus  # noqa: E402
import graph_registry  # noqa: E402
import references  # noqa: E402

GRAPH_DIR = corpus_config.SPIRES_GRAPH_DIR
PREFIX = corpus_config.NODE_PREFIX
L = corpus_config.label   # "Norm" -> "SP_Norm"; also used for relationship types

# Reuse the verified extractions when present -- they carry the recovered exceptions --
# and fall back to the merely cleaned ones for any provision the verifier had to skip.
VERIFIED = corpus_config.provision_glob(".verified.yaml")
CLEANED = corpus_config.provision_glob(".clean.yaml")

_WS = re.compile(r"\s+")


def decode(value):
    """SPIRES writes NamedEntity slots as `AUTO:use%20or%20disclose`."""
    if not isinstance(value, str):
        return None
    text = value.split(":", 1)[-1] if value.startswith("AUTO:") else value
    return _WS.sub(" ", urllib.parse.unquote(text)).strip() or None


def vocab_key(kind, name):
    """Stable id for a shared vocabulary node, case-folded so spellings converge."""
    return "%s:%s" % (kind, _WS.sub(" ", name.lower()).strip())


def load_provisions():
    """One cleaned provision per id, preferring the verified file."""
    out = {}
    for pattern in (CLEANED, VERIFIED):          # verified second so it wins
        for path in sorted(glob.glob(pattern)):
            with io.open(path, encoding="utf-8") as fh:
                document = yaml.safe_load(fh)
            if not document or not document.get("provision_id"):
                continue
            out[str(document["provision_id"])] = document
    return out


def build(provisions, texts=None):
    """Nodes and edges for the norm graph.

    `texts` puts the verbatim provision text on the :SP_Provision nodes. Without it the
    spires arm has to read text out of the :AppNode graph instead, which quietly makes
    the EDC construction path a prerequisite for the SPIRES one -- so a new corpus would
    need both graphs built to run the arm that only uses one of them.
    """
    nodes, edges = {}, []
    counts = collections.Counter()
    texts = texts or {}

    def node(key, label, **props):
        if key not in nodes:
            nodes[key] = {"key": key, "label": label, **props}
            counts[label] += 1
        return key

    def edge(src, rel, dst, **props):
        edges.append({"from_key": src, "rel": rel, "to_key": dst, **props})
        counts[rel] += 1

    for pid, document in sorted(provisions.items()):
        prov_key = "provision:%s" % pid
        node(prov_key, L("Provision"), name=corpus_config.provision_key(pid), provision=pid,
             text_raw=texts.get(pid, ""))

        for index, norm in enumerate(document.get("norms") or [], 1):
            modality = (norm.get("deontic_modality") or "UNSPECIFIED").strip()
            norm_key = "norm:%s:%d" % (pid, index)
            node(norm_key, L("Norm"), name="%s %s#%d" % (modality, pid, index),
                 modality=modality, provision=pid,
                 scope_qualifier=norm.get("scope_qualifier") or "")
            edge(prov_key, L("HAS_NORM"), norm_key)

            for slot, label, rel in (("bearer", L("Actor"), L("BEARER")),
                                     ("action", L("Action"), L("ACTION")),
                                     ("object", L("InfoType"), L("OBJECT"))):
                name = decode(norm.get(slot))
                if not name:
                    continue
                key = vocab_key(label, name)
                node(key, label, name=name)
                edge(norm_key, rel, key)

            for slot, label, rel in (("conditions", L("Condition"), L("CONDITION")),
                                     ("exceptions", L("Exemption"), L("EXCEPTION"))):
                for item_index, item in enumerate(norm.get(slot) or [], 1):
                    statement = (item.get("statement") or "").strip()
                    if not statement:
                        continue
                    key = "%s:%s:%d:%d" % (label.lower(), pid, index, item_index)
                    node(key, label, name=statement[:70], statement=statement,
                         source_provision=str(item.get("source_provision") or ""),
                         verified=bool(item.get("verified")))
                    edge(norm_key, rel, key)

    return nodes, edges, counts


def strip_key_prefix(key):
    """`subclause 6.2` -> `6.2`. The inverse of corpus_config.provision_key.

    Was `.replace("subclause ", "")` in three places, which is a statement about one
    instrument's citation vocabulary sitting in a graph builder.
    """
    prefix = corpus_config.PROVISION_KEY_PREFIX
    text = str(key).strip()
    if prefix and text.lower().startswith(prefix.lower() + " "):
        text = text[len(prefix) + 1:]
    return text.strip()


def add_reference_edges(nodes, edges, counts, provisions):
    """Cross-reference edges from the citation parser, which measured far better than
    SPIRES extraction: over the 62 Schedule 1 clauses the regex finds references in 26
    provisions against SPIRES' 10, with one provision that only SPIRES found.

    Citations that leave the corpus become :SP_ExternalRef stubs rather than vanishing.
    Part IIIC makes 25 of them -- 17 into other Acts, 8 into other Parts of the Privacy
    Act -- and the previous behaviour was to drop all of them and print a count, so the
    graph understated its own incompleteness. A stub carries the citation string, which
    is enough for retrieval to say "this provision depends on something not in the graph".
    """
    raw = prep_corpus.provisions()
    known = {pid for pid in provisions}
    parser = references.ReferenceParser(prep_corpus.load_profile(), sorted(known))
    added = skipped = external = 0
    for pid, text in raw.items():
        if pid not in known:
            continue
        for citation in parser.parse(text, pid):
            if citation.kind == "internal" and citation.target != pid:
                edges.append({"from_key": "provision:%s" % pid, "rel": L("REFERS_TO"),
                              "to_key": "provision:%s" % citation.target})
                counts[L("REFERS_TO")] += 1
                added += 1
            elif citation.kind in ("external", "unresolved"):
                label = citation.instrument or citation.raw
                key = "external:%s" % re.sub(r"[^0-9A-Za-z]+", "_", label.lower()).strip("_")
                if key not in nodes:
                    nodes[key] = {"key": key, "label": L("ExternalRef"), "name": label,
                                  "instrument": citation.instrument or ""}
                    counts[L("ExternalRef")] += 1
                edges.append({"from_key": "provision:%s" % pid,
                              "rel": L("REFERS_EXTERNAL"), "to_key": key})
                counts[L("REFERS_EXTERNAL")] += 1
                external += 1
            else:
                skipped += 1

    # `disapplies` is the one edge SPIRES produces that the regex cannot: it distinguishes
    # "this switches that off" from a bare mention, which is a semantic call, not a
    # pattern. Kept alongside SP_REFERS_TO rather than instead of it.
    for pid, document in sorted(provisions.items()):
        for target in (document.get("disapplies") or []):
            target_pid = strip_key_prefix(str(target))
            if target_pid in known and target_pid != pid:
                edges.append({"from_key": "provision:%s" % pid,
                              "rel": L("DISAPPLIES"),
                              "to_key": "provision:%s" % target_pid})
                counts[L("DISAPPLIES")] += 1
    return added, skipped, external


def write_csvs(nodes, edges):
    os.makedirs(GRAPH_DIR, exist_ok=True)
    # DictWriter is created with extrasaction="ignore", so a property missing from this
    # list is dropped without a word. text_raw was added and silently did not appear.
    node_fields = ["key", "label", "name", "modality", "provision", "scope_qualifier",
                   "statement", "source_provision", "verified", "text_raw", "instrument"]
    with io.open(os.path.join(GRAPH_DIR, "nodes.csv"), "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, node_fields, extrasaction="ignore")
        writer.writeheader()
        for value in nodes.values():
            writer.writerow(value)
    with io.open(os.path.join(GRAPH_DIR, "edges.csv"), "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, ["from_key", "rel", "to_key"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(edges)


def load(nodes, edges, wipe, creds_path=None, force_prefix=False):
    from credentials import load_credentials
    from neo4j import GraphDatabase

    creds = load_credentials(creds_path)
    if not creds.get("NEO4J_URI"):
        raise SystemExit(
            "No Neo4j credentials. Dot-source setup_env.ps1, or pass --creds with the "
            "path to the Aura credentials file.")
    driver = GraphDatabase.driver(creds["NEO4J_URI"],
                                  auth=(creds["NEO4J_USERNAME"], creds["NEO4J_PASSWORD"]))
    try:
        with driver.session(database=creds.get("NEO4J_DATABASE") or "neo4j") as session:
            # Claim the prefix before writing a single node. Neo4j has no namespaces, so
            # a second corpus loading under a claimed prefix would MERGE into the first
            # on any key collision -- silently, and with no way to tell the two apart
            # afterwards. Checked against both the registry and the labels actually
            # present, since a registry of intentions cannot see a graph loaded before it.
            problems = graph_registry.check(corpus_config.NAME, PREFIX, session)
            if problems and not force_prefix:
                raise SystemExit(
                    "Refusing to load: %s" % NL.join(problems) + NL +
                    "Run `python graph_registry.py` to see what is claimed, change "
                    "node_prefix in corpus.yaml, or pass --force-prefix if you are "
                    "certain.")
            graph_registry.claim(corpus_config.NAME, PREFIX, corpus_config.DESCRIPTION,
                                 session, force=True)

            if wipe:
                # Only SP_*; :AppNode and :RP1Entity share this instance and must survive.
                removed = session.run(
                    "MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH $p) "
                    "DETACH DELETE n RETURN count(n) AS c", p=PREFIX).single()["c"]
                print("wiped %d existing %s node(s)" % (removed, PREFIX))

            by_label = collections.defaultdict(list)
            for value in nodes.values():
                by_label[value["label"]].append(value)
            for label, rows in sorted(by_label.items()):
                session.run(
                    "UNWIND $rows AS row MERGE (n:`%s` {key: row.key}) SET n += row" % label,
                    rows=rows)
                print("  %-14s %4d nodes" % (label, len(rows)))

            by_rel = collections.defaultdict(list)
            for e in edges:
                by_rel[e["rel"]].append(e)
            for rel, rows in sorted(by_rel.items()):
                # The relationship type cannot be parameterised, and the query contains
                # literal braces, so it is concatenated rather than .format()-ed.
                session.run(
                    "UNWIND $rows AS row MATCH (a {key: row.from_key}) "
                    "MATCH (b {key: row.to_key}) MERGE (a)-[:`" + rel + "`]->(b)",
                    rows=rows)
                print("  %-14s %4d edges" % (rel, len(rows)))

            total = session.run(
                "MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH $p) "
                "RETURN count(n) AS c", p=PREFIX).single()["c"]
            print("\nin Aura: %d %s nodes" % (total, PREFIX))
    finally:
        driver.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Write CSVs, do not load.")
    ap.add_argument("--wipe", action="store_true", help="Delete existing SP_* first.")
    ap.add_argument("--force-prefix", action="store_true",
                    help="Load even if the node prefix is claimed by another corpus. "
                         "This merges two graphs; there is no way back short of a wipe.")
    ap.add_argument("--creds", default=None,
                    help="Aura credentials file; the environment wins over it.")
    args = ap.parse_args()

    provisions = load_provisions()
    texts = prep_corpus.provisions()
    nodes, edges, counts = build(provisions, texts)
    added, skipped, external = add_reference_edges(nodes, edges, counts, provisions)
    write_csvs(nodes, edges)

    print("provisions        : %d" % len(provisions))
    print("\nnodes")
    for label in [L(x) for x in ("Provision", "Norm", "Actor", "Action", "InfoType",
                                 "ExternalRef",
                                 "Condition", "Exemption")]:
        print("  %-14s %4d" % (label, counts.get(label, 0)))
    print("\nedges")
    for rel in [L(x) for x in ("HAS_NORM", "BEARER", "ACTION", "OBJECT", "CONDITION",
                               "EXCEPTION", "REFERS_TO", "DISAPPLIES", "REFERS_EXTERNAL")]:
        print("  %-14s %4d" % (rel, counts.get(rel, 0)))
    print("\ntotals            : %d nodes, %d edges" % (len(nodes), len(edges)))
    print("cross-refs added   : %d" % added)
    print("external stubs     : %d edge(s) to provisions outside this corpus" % external)
    print("self-references    : %d (dropped, as they carry no edge)" % skipped)
    print("csvs              : %s" % GRAPH_DIR)

    if args.dry_run:
        print("\nDry run -- nothing loaded.")
        return 0
    load(nodes, edges, args.wipe, args.creds, args.force_prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
