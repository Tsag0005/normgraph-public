"""Which corpus owns which node prefix.

Several corpora share one Aura instance -- :AppNode, :RP1Entity and :SP_* already do --
and Neo4j has no notion of a namespace. `MERGE (n:X {key: row.key}) SET n += row` will
happily fold a second corpus into a first if their prefixes match and a key collides, and
nothing anywhere would report it. The graph would simply contain two statutes claiming to
be one, and the first sign of trouble would be a retrieval returning provisions from an
instrument nobody asked about.

So the prefix is claimed before it is written to, and the claim is checked against two
things: this file, and the labels actually present in the instance. The second matters
because a registry that only records intentions cannot notice a graph loaded before the
registry existed.

Reserved prefixes are the labels already in use by the EDC and RP1 graphs, which predate
prefixing entirely and would be caught by a generated prefix of the wrong shape.

    python graph_registry.py                  # what is claimed, and what is live
    python graph_registry.py --check          # would the configured corpus be allowed?
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus_config  # noqa: E402

REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "corpora", "corpora.yaml")

# Labels that predate prefixing. The EDC graph writes :AppNode plus a type sub-label; the
# RP1 graph writes :RP1Entity. A generated prefix must not shadow any of them.
RESERVED = ("AppNode", "RP1Entity", "Concept", "InformationType", "Legislation",
            "Party", "Principle", "Provision")

_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{0,7}_$")


class RegistryError(RuntimeError):
    """The prefix cannot be claimed. Always fatal: the alternative is a merged graph."""


def load() -> dict:
    if not os.path.exists(REGISTRY_PATH):
        return {}
    try:
        import yaml
        with io.open(REGISTRY_PATH, encoding="utf-8") as fh:
            return (yaml.safe_load(fh) or {}).get("corpora") or {}
    except Exception:  # noqa: BLE001 - an unreadable registry must not be read as "empty"
        raise RegistryError(
            "%s exists but could not be read. Refusing to claim a prefix on the "
            "assumption that nothing is registered." % REGISTRY_PATH)


def save(corpora: dict) -> None:
    import yaml
    header = [
        "# Which corpus owns which Neo4j node prefix.",
        "#",
        "# Written by graph_registry.claim(), read before any graph load. Several corpora",
        "# share one Aura instance and Neo4j has no namespaces, so two corpora claiming one",
        "# prefix would merge into a single graph with no error reported anywhere.",
        "",
    ]
    with io.open(REGISTRY_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(header))
        yaml.safe_dump({"corpora": corpora}, fh, sort_keys=True, allow_unicode=True,
                       default_flow_style=False)


def validate_prefix(prefix: str) -> None:
    if not _PREFIX_RE.match(prefix or ""):
        raise RegistryError(
            "node_prefix %r is not usable: it must be 1-8 upper-case alphanumerics "
            "followed by an underscore, e.g. 'SP_'. A prefix without the underscore "
            "cannot be told apart from a label that merely starts with the same letters."
            % prefix)
    for reserved in RESERVED:
        if reserved.upper().startswith(prefix[:-1].upper()):
            raise RegistryError(
                "node_prefix %r would shadow the existing label :%s, which belongs to the "
                "EDC or RP1 graph and predates prefixing. Choose another."
                % (prefix, reserved))


def live_prefixes(session) -> dict:
    """Prefix -> label count, for every prefixed label actually in the instance."""
    out = {}
    for row in session.run("CALL db.labels() YIELD label RETURN label"):
        label = row["label"]
        match = re.match(r"^([A-Z][A-Z0-9]{0,7}_)", label)
        if match:
            out[match.group(1)] = out.get(match.group(1), 0) + 1
    return out


def check(name: str, prefix: str, session=None) -> list:
    """Problems with claiming `prefix` for `name`. Empty means it is safe to load."""
    problems = []
    try:
        validate_prefix(prefix)
    except RegistryError as exc:
        problems.append(str(exc))
        return problems

    registered = load()
    for other, entry in registered.items():
        if other != name and entry.get("node_prefix") == prefix:
            problems.append(
                "node_prefix %r is already claimed by corpus %r (%s). Loading under it "
                "would merge the two graphs. Change node_prefix in corpus.yaml."
                % (prefix, other, entry.get("description") or "no description"))

    if session is not None:
        live = live_prefixes(session)
        if prefix in live and name not in registered:
            problems.append(
                "%d label(s) with prefix %r already exist in the instance, but no corpus "
                "is registered as owning it. Something was loaded before the registry "
                "existed. Confirm what it is, then re-run with --claim to record it."
                % (live[prefix], prefix))
    return problems


def claim(name: str, prefix: str, description: str = "", session=None,
          force: bool = False) -> None:
    """Record the claim, refusing unless it is safe."""
    problems = check(name, prefix, session)
    if problems and not force:
        raise RegistryError("\n".join(problems))
    registered = load()
    registered[name] = {
        "node_prefix": prefix,
        "description": description or "",
        "claimed": _dt.datetime.now().strftime("%Y-%m-%d"),
    }
    save(registered)


def main() -> int:
    ap = argparse.ArgumentParser(description="Node-prefix ownership across corpora.")
    ap.add_argument("--check", action="store_true",
                    help="Would the configured corpus be allowed to load?")
    ap.add_argument("--claim", action="store_true",
                    help="Record the configured corpus's claim on its prefix.")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    name, prefix = corpus_config.NAME, corpus_config.NODE_PREFIX
    registered = load()

    print("registry : %s" % REGISTRY_PATH)
    if registered:
        for other, entry in sorted(registered.items()):
            print("   %-24s %-6s %s" % (other, entry.get("node_prefix"),
                                        entry.get("description", "")[:48]))
    else:
        print("   (nothing registered yet)")

    session = None
    driver = None
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001
        pass
    uri = os.environ.get("NEO4J_URI")
    if uri:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            uri, auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
        session = driver.session(database=os.environ.get("NEO4J_DATABASE") or "neo4j")
        print("\nlive in %s:" % os.environ.get("NEO4J_DATABASE"))
        for live_prefix, count in sorted(live_prefixes(session).items()):
            owner = next((n for n, e in registered.items()
                          if e.get("node_prefix") == live_prefix), "UNCLAIMED")
            print("   %-6s %2d label(s)   owner: %s" % (live_prefix, count, owner))
    else:
        print("\n(no NEO4J_URI; skipping the live check)")

    status = 0
    try:
        if args.claim:
            claim(name, prefix, corpus_config.DESCRIPTION, session, args.force)
            print("\nclaimed %r for corpus %r" % (prefix, name))
        else:
            problems = check(name, prefix, session)
            print("\nconfigured corpus %r wants %r" % (name, prefix))
            if problems:
                status = 1
                for problem in problems:
                    print("   REFUSED: %s" % problem)
            else:
                print("   OK to load")
    except RegistryError as exc:
        status = 1
        print("\nREFUSED: %s" % exc, file=sys.stderr)
    finally:
        if driver is not None:
            driver.close()
    return status


if __name__ == "__main__":
    sys.exit(main())
