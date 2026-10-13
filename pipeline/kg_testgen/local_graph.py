"""Read a SPIRES graph from the CSVs produced by build_spires_graph.

The browser owns the durable copy of these files. A job uploads them into its
temporary workspace before this reader is constructed.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from pathlib import Path

import corpus_config

from .neo4j_client import GraphUnavailable


class _Schema:
    def __init__(self, nodes, edges):
        self.nodes = nodes
        self.edges = edges

    def as_dict(self):
        return {
            "backend": "local",
            "labels": sorted({n["label"] for n in self.nodes.values()}),
            "relationship_types": sorted({e["rel"] for e in self.edges}),
            "node_count": len(self.nodes),
            "relationship_count": len(self.edges),
        }


class LocalGraphClient:
    """The four read operations used by the SPIRES context renderer."""

    def __init__(self, _secrets=None, _config=None):
        self.directory = Path(os.environ.get("LOCAL_GRAPH_DIR") or corpus_config.SPIRES_GRAPH_DIR)
        self.nodes = {}
        self.edges = []
        self.outgoing = defaultdict(list)
        self.incoming = defaultdict(list)
        self.provisions = {}
        self.schema = None

    def __enter__(self):
        node_path, edge_path = self.directory / "nodes.csv", self.directory / "edges.csv"
        if not node_path.is_file() or not edge_path.is_file():
            raise GraphUnavailable("Local graph is missing. Build or import this corpus before previewing.")
        with node_path.open(encoding="utf-8-sig", newline="") as stream:
            self.nodes = {row["key"]: row for row in csv.DictReader(stream)}
        with edge_path.open(encoding="utf-8-sig", newline="") as stream:
            self.edges = list(csv.DictReader(stream))
        for edge in self.edges:
            self.outgoing[edge["from_key"]].append(edge)
            self.incoming[edge["to_key"]].append(edge)
        label = corpus_config.label("Provision")
        self.provisions = {row["provision"]: row for row in self.nodes.values()
                           if row.get("label") == label and row.get("provision")}
        if not self.provisions:
            raise GraphUnavailable(f"No {label} provisions are present in the local graph.")
        self.schema = _Schema(self.nodes, self.edges)
        return self

    def __exit__(self, *_args):
        return None

    def _first_target(self, key, relation, label):
        for edge in self.outgoing[key]:
            target = self.nodes.get(edge["to_key"])
            if edge["rel"] == corpus_config.label(relation) and target and target["label"] == corpus_config.label(label):
                return target.get("name") or ""
        return ""

    def norm_rows(self, pid):
        provision = self.provisions.get(pid)
        if not provision:
            return []
        rows = []
        for edge in self.outgoing[provision["key"]]:
            norm = self.nodes.get(edge["to_key"])
            if edge["rel"] != corpus_config.label("HAS_NORM") or not norm or norm["label"] != corpus_config.label("Norm"):
                continue
            key = norm["key"]
            rows.append({"key": key, "modality": norm.get("modality"),
                         "scope": norm.get("scope_qualifier"),
                         "bearer": self._first_target(key, "BEARER", "Actor"),
                         "action": self._first_target(key, "ACTION", "Action"),
                         "object": self._first_target(key, "OBJECT", "InfoType")})
        return rows

    def attached_rows(self, keys, relation):
        rows = []
        rel = corpus_config.label(relation)
        for key in keys:
            for edge in self.outgoing[key]:
                if edge["rel"] != rel:
                    continue
                target = self.nodes.get(edge["to_key"])
                if target:
                    rows.append({"key": key, "statement": target.get("statement"),
                                 "source_provision": target.get("source_provision")})
        return rows

    def neighbours(self, pid):
        provision = self.provisions.get(pid)
        if not provision:
            return []
        rows = set()
        relevant = {corpus_config.label("REFERS_TO"), corpus_config.label("DISAPPLIES")}
        for direction, edges, endpoint in (("outbound", self.outgoing[provision["key"]], "to_key"),
                                           ("inbound", self.incoming[provision["key"]], "from_key")):
            for edge in edges:
                target = self.nodes.get(edge[endpoint])
                if edge["rel"] in relevant and target and target["label"] == corpus_config.label("Provision"):
                    rows.add((target["provision"], edge["rel"], direction))
        return [{"pid": item[0], "rel": item[1], "direction": item[2]} for item in sorted(rows)]

    def text_rows(self, pids):
        return [{"pid": pid, "name": row.get("name"), "text_raw": row.get("text_raw")}
                for pid in pids if (row := self.provisions.get(pid)) and row.get("text_raw")]
