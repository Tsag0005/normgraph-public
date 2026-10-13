"""Neo4j AuraDB client: connection, schema introspection, capability probing.

Nothing about the graph's shape is hardcoded. Labels, relationship types and property
keys are read from the live instance at startup and logged, and the configured node
label is checked against what is actually there before any query runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:  # Aura presents a TLS chain that Python's OpenSSL may reject behind an
    import truststore  # inspecting proxy; route verification via the OS trust store.
    truststore.inject_into_ssl()
except ImportError:
    pass

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired

from .config import Config, Secrets

logger = logging.getLogger(__name__)


class GraphUnavailable(RuntimeError):
    """The database could not be reached or authenticated. Always fatal."""


@dataclass
class SchemaInfo:
    """What the live instance actually contains, as read at startup."""
    labels: List[str] = field(default_factory=list)
    relationship_types: List[str] = field(default_factory=list)
    node_property_keys: List[str] = field(default_factory=list)
    relationship_property_keys: List[str] = field(default_factory=list)
    indexes: List[Dict[str, str]] = field(default_factory=list)
    vector_index_supported: bool = False
    vector_indexes_present: List[str] = field(default_factory=list)
    apoc_available: bool = False
    gds_available: bool = False
    fulltext_available: bool = False
    node_count: int = 0
    relationship_count: int = 0

    def as_dict(self) -> dict:
        return {
            "labels": self.labels,
            "relationship_types": self.relationship_types,
            "node_property_keys": self.node_property_keys,
            "relationship_property_keys": self.relationship_property_keys,
            "indexes": self.indexes,
            "vector_index_supported": self.vector_index_supported,
            "vector_indexes_present": self.vector_indexes_present,
            "apoc_available": self.apoc_available,
            "gds_available": self.gds_available,
            "fulltext_available": self.fulltext_available,
            "node_count": self.node_count,
            "relationship_count": self.relationship_count,
        }


class Neo4jClient:
    def __init__(self, secrets: Secrets, config: Config) -> None:
        self._secrets = secrets
        self._config = config
        self._driver = None
        self.schema: Optional[SchemaInfo] = None

    # -- lifecycle ---------------------------------------------------------------
    def connect(self) -> None:
        try:
            self._driver = GraphDatabase.driver(
                self._secrets.neo4j_uri,
                auth=(self._secrets.neo4j_user, self._secrets.neo4j_password),
            )
            self._driver.verify_connectivity()
        except AuthError as exc:
            raise GraphUnavailable(
                "Neo4j rejected the credentials. Check NEO4J_USERNAME and "
                "NEO4J_PASSWORD.\n  %s" % exc) from exc
        except (ServiceUnavailable, SessionExpired) as exc:
            raise GraphUnavailable(
                "Could not reach the Neo4j instance at %s.\n"
                "AuraDB free-tier instances pause after inactivity -- open the Aura "
                "console and resume the instance, then retry.\n  %s"
                % (self._secrets.neo4j_uri, exc)) from exc
        except Exception as exc:  # noqa: BLE001 - surface anything else with context
            raise GraphUnavailable(
                "Failed to connect to Neo4j at %s: %s" % (self._secrets.neo4j_uri, exc)) from exc
        logger.info("Connected to Neo4j at %s (database: %s)",
                    self._secrets.neo4j_uri, self._secrets.neo4j_database)

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()

    def __enter__(self) -> "Neo4jClient":
        self.connect()
        self.schema = self.introspect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- querying ----------------------------------------------------------------
    def run(self, cypher: str, **params: Any) -> List[Dict[str, Any]]:
        """Run a parameterised query. Callers never concatenate input into `cypher`."""
        if self._driver is None:
            raise GraphUnavailable("connect() must be called before running queries")
        try:
            with self._driver.session(database=self._secrets.neo4j_database) as session:
                return [record.data() for record in session.run(cypher, **params)]
        except (ServiceUnavailable, SessionExpired) as exc:
            raise GraphUnavailable(
                "Lost connection to Neo4j mid-query. A free-tier instance may have "
                "paused.\n  %s" % exc) from exc

    def _scalar(self, cypher: str, key: str, default: Any = None, **params: Any) -> Any:
        rows = self.run(cypher, **params)
        return rows[0][key] if rows else default

    # -- introspection -----------------------------------------------------------
    def introspect(self) -> SchemaInfo:
        """Read the live schema and probe optional capabilities. Never assumes."""
        info = SchemaInfo()

        info.labels = sorted(self._scalar(
            "CALL db.labels() YIELD label RETURN collect(label) AS v", "v", []) or [])
        info.relationship_types = sorted(self._scalar(
            "CALL db.relationshipTypes() YIELD relationshipType "
            "RETURN collect(relationshipType) AS v", "v", []) or [])
        info.node_count = self._scalar("MATCH (n) RETURN count(n) AS v", "v", 0)
        info.relationship_count = self._scalar("MATCH ()-[r]->() RETURN count(r) AS v", "v", 0)

        label = self._config.node_label
        info.node_property_keys = sorted(self._scalar(
            "MATCH (n:`%s`) UNWIND keys(n) AS k RETURN collect(DISTINCT k) AS v" % label,
            "v", []) or [])
        info.relationship_property_keys = sorted(self._scalar(
            "MATCH (:`%s`)-[r]->(:`%s`) UNWIND keys(r) AS k "
            "RETURN collect(DISTINCT k) AS v" % (label, label), "v", []) or [])

        try:
            info.indexes = [{"name": r.get("name"), "type": r.get("type")}
                            for r in self.run("SHOW INDEXES YIELD name, type RETURN name, type")]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not list indexes: %s", exc)

        procedures = self._probe_procedures()
        info.apoc_available = any(p.startswith("apoc.") for p in procedures)
        info.gds_available = any(p.startswith("gds.") for p in procedures)
        info.fulltext_available = "db.index.fulltext.queryNodes" in procedures
        info.vector_index_supported = "db.index.vector.queryNodes" in procedures
        info.vector_indexes_present = [
            i["name"] for i in info.indexes if (i.get("type") or "").upper() == "VECTOR"]

        self._log_schema(info)
        self._validate_label(info)
        return info

    def _probe_procedures(self) -> set:
        try:
            rows = self.run("SHOW PROCEDURES YIELD name RETURN name")
            return {r["name"] for r in rows}
        except Exception as exc:  # noqa: BLE001 - restricted instances may refuse
            logger.warning("Could not enumerate procedures; assuming none available: %s", exc)
            return set()

    def _log_schema(self, info: SchemaInfo) -> None:
        logger.info("Graph contains %d nodes and %d relationships",
                    info.node_count, info.relationship_count)
        logger.info("Node labels: %s", ", ".join(info.labels) or "(none)")
        logger.info("Relationship types: %d distinct", len(info.relationship_types))
        logger.info("Node property keys on :%s: %s",
                    self._config.node_label, ", ".join(info.node_property_keys) or "(none)")
        logger.info("Relationship property keys: %s",
                    ", ".join(info.relationship_property_keys) or "(none)")
        logger.info("Capabilities: APOC=%s  GDS=%s  fulltext=%s  vector_index=%s",
                    info.apoc_available, info.gds_available,
                    info.fulltext_available, info.vector_index_supported)
        if info.vector_index_supported and not info.vector_indexes_present:
            logger.info("Vector indexes are supported but none exist; vector seeding "
                        "remains an unimplemented extension point.")

    def _validate_label(self, info: SchemaInfo) -> None:
        label = self._config.node_label
        if label not in info.labels:
            raise GraphUnavailable(
                "Configured node label :%s is not present in the graph.\n"
                "Labels actually present: %s\n"
                "Set --node-label to one of these."
                % (label, ", ".join(info.labels) or "(none)"))
        required = {"key", "name"}
        absent = required - set(info.node_property_keys)
        if absent:
            raise GraphUnavailable(
                "Nodes labelled :%s are missing required propert%s: %s.\n"
                "Seeding matches on `key` and renders `name`."
                % (label, "y" if len(absent) == 1 else "ies", ", ".join(sorted(absent))))
