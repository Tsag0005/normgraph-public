"""Strict corpus discovery and UI-owned workspaces. Run with the pipeline Python."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil

import yaml

UI = Path(__file__).resolve().parents[2]
# ROOT is the pipeline, not the repository: everything this module resolves -- configs,
# statutes, profiles, the schema -- is the pipeline's, and corpus configs express their
# paths relative to it.
ROOT = UI.parent / "pipeline"
CORPORA = ROOT / "corpora"
DATA = Path(os.environ.get("PIPELINE_UI_DATA_DIR") or UI / "data").resolve()


def read_yaml(path):
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping: {path}")
    return value


def absolute(value):
    return str((ROOT / value).resolve())


def config_paths():
    return sorted(set([CORPORA / "corpus.yaml", *CORPORA.glob("corpus.*.yaml"),
                       *(DATA / "corpora").glob("*/corpus.yaml")]))


def strict_config(path):
    cfg = read_yaml(path)
    for key in ("name", "file_prefix", "node_prefix"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            raise ValueError(f"{path.name}: missing {key}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cfg["file_prefix"]):
        raise ValueError("File prefix must contain letters, numbers, underscores or hyphens")
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,7}_", cfg["node_prefix"]):
        raise ValueError("Node prefix must be 1–8 uppercase letters/numbers followed by _")
    paths = cfg.get("paths") or {}
    if not paths.get("raw") or not paths.get("profile"):
        raise ValueError(f"{path.name}: paths.raw and paths.profile are required")
    pattern = (cfg.get("provision") or {}).get("pattern")
    if not pattern or re.compile(pattern).groups < 1:
        raise ValueError("Provision pattern needs a capture group for the identifier")
    cfg["paths"] = {k: absolute(v) for k, v in paths.items()}
    cfg["paths"].setdefault("schema", str(ROOT / "spires" / "norm.yaml"))
    cfg["paths"].setdefault("spires_dir", str(ROOT / "spires"))
    if Path(path).resolve().is_relative_to(DATA):
        for key in ("raw", "profile", "spires_dir"):
            if not Path(cfg["paths"][key]).resolve().is_relative_to(DATA):
                raise ValueError(f"Visitor corpus {key} must stay inside its workspace")
        if Path(cfg["paths"]["schema"]).resolve() != (ROOT / "spires/norm.yaml").resolve():
            raise ValueError("Visitor corpora must use the bundled SPIRES schema")
    return cfg


def identifier(path):
    resolved = path.resolve()
    # Browser exports can be restored into another visitor's temporary path.
    # Keep corpus IDs stable across that move.
    identity = ("visitor:" + resolved.relative_to(DATA).as_posix()
                if resolved.is_relative_to(DATA) else "builtin:" + resolved.name)
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def workspace(corpus_id):
    return DATA / "workspaces" / corpus_id


def artifacts(directory, prefix):
    groups = {"raw": {}, "clean": {}, "verified": {}}
    for path in Path(directory).glob(prefix + "_*.yaml"):
        kind = "verified" if path.name.endswith(".verified.yaml") else "clean" if path.name.endswith(".clean.yaml") else "raw"
        groups[kind][path.name] = path
    return groups


def discover():
    items = []
    for path in config_paths():
        if not path.exists():
            continue
        cid = identifier(path)
        try:
            cfg = strict_config(path)
            merged = artifacts(Path(cfg["paths"]["spires_dir"]) / "output", cfg["file_prefix"])
            local = artifacts(workspace(cid) / "spires" / "output", cfg["file_prefix"])
            for key in merged:
                merged[key].update(local[key])
            counts = {k: len(v) for k, v in merged.items()}
            items.append({"id": cid, "configPath": str(path), "name": cfg["name"],
                          "description": cfg.get("description", ""), "filePrefix": cfg["file_prefix"],
                          "nodePrefix": cfg["node_prefix"], "profile": cfg["paths"]["profile"],
                          "profileExists": Path(cfg["paths"]["profile"]).is_file(),
                          "path": cfg["paths"]["raw"], "sourceExists": Path(cfg["paths"]["raw"]).is_file(),
                          "builtIn": not path.is_relative_to(DATA), "artifacts": counts,
                          "buildStatus": "Local graph ready" if (workspace(cid) / "spires/graph/ready.json").exists() or
                          (ROOT.parent / "seed_graphs" / cfg["name"] / "nodes.csv").exists()
                          else "CSV available" if (workspace(cid) / "spires/graph/nodes.csv").exists()
                          else "Extractions available" if any(counts.values()) else "Not built",
                          "errors": []})
        except Exception as exc:
            items.append({"id": cid, "name": path.name, "configPath": str(path), "errors": [str(exc)]})
    collisions = {x['id']: [] for x in items}
    for item in items:
        if item["errors"]:
            continue
        for other in items:
            if other is not item and not other["errors"] and other.get("nodePrefix") == item["nodePrefix"]:
                collisions[item['id']].append(f"Prefix also configured by {other['name']}")
            if other is not item and not other["errors"] and other.get("name") == item["name"]:
                collisions[item['id']].append("Corpus names must be unique across configurations")
            if (other is not item and not other["errors"] and other.get("filePrefix") == item["filePrefix"]):
                collisions[item['id']].append(f"File prefix also configured by {other['name']}; artifact reuse would be ambiguous")
    for item in items:
        item['errors'].extend(collisions[item['id']])
    return items


def resolve(corpus_id):
    item = next((x for x in discover() if x["id"] == corpus_id), None)
    if not item:
        raise ValueError("Unknown corpus. Refresh the corpus list.")
    if item["errors"]:
        raise ValueError("; ".join(item["errors"]))
    return item, strict_config(Path(item["configPath"]))


def prepare(corpus_id):
    item, cfg = resolve(corpus_id)
    target = workspace(corpus_id)
    target.mkdir(parents=True, exist_ok=True)
    stamp = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode())
    for key in ("raw", "profile", "schema"):
        path = Path(cfg["paths"][key])
        if path.exists():
            stamp.update(path.read_bytes())
    manifest = target / "source-fingerprint.txt"
    fingerprint = stamp.hexdigest()
    if manifest.exists() and manifest.read_text() != fingerprint:
        raise ValueError("Source, schema or profile changed since this workspace was created. "
                         "Add the revised instrument as a new corpus with a unique prefix to avoid reusing stale extractions.")
    manifest.write_text(fingerprint)
    # Preserve the selected source configuration; only our copy redirects outputs.
    source_output = Path(cfg["paths"]["spires_dir"]) / "output"
    output = target / "spires/output"
    output.mkdir(parents=True, exist_ok=True)
    for group in artifacts(source_output, cfg["file_prefix"]).values():
        for name, path in group.items():
            destination = output / name
            if not destination.exists():
                shutil.copy2(path, destination)
    cfg["paths"]["spires_dir"] = str(target / "spires")
    source = Path(cfg["paths"]["raw"])
    snapshot = target / "source.txt"
    shutil.copy2(source, snapshot)
    cfg["paths"]["raw"] = str(snapshot)
    profile = Path(cfg["paths"]["profile"])
    if profile.exists():
        destination = target / "profile.yaml"
        shutil.copy2(profile, destination)
        cfg["paths"]["profile"] = str(destination)
    else:
        cfg["paths"]["profile"] = str(target / "profile.yaml")
    config = target / "corpus.yaml"
    config.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return item, cfg, config


if __name__ == "__main__":
    print(json.dumps(discover(), ensure_ascii=False))
