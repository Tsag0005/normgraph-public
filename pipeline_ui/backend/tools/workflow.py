"""Official pipeline orchestration with UI-owned outputs and guarded loading.

Process-local build seams stop graph construction at CSVs and reuse verification.
No pipeline source or pipeline-owned artifact/registry file is changed.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from corpora import DATA, ROOT, discover, prepare, read_yaml, resolve, workspace


def emit(stage, message):
    print(f"[{stage}] {message}", flush=True)


def run(command):
    emit("command", subprocess.list2cmdline(command))
    result = subprocess.run(command, cwd=ROOT, env=dict(os.environ))
    if result.returncode:
        raise RuntimeError(f"Command exited {result.returncode}. See the stage log above.")


def registry_checks():
    import graph_registry
    original = graph_registry.load
    local_path = DATA / "registry.yaml"
    def combined():
        entries = original()
        if local_path.exists():
            for name, entry in (read_yaml(local_path).get("corpora") or {}).items():
                if name in entries and entries[name]["node_prefix"] != entry["node_prefix"]:
                    raise ValueError(f"Registry ownership conflict for {name}")
                entries[name] = entry
        return entries
    def save(entries):
        import yaml
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(yaml.safe_dump({"corpora": entries}), encoding="utf-8")
    graph_registry.load = combined
    graph_registry.save = save
    return graph_registry


def connection():
    from credentials import load_credentials
    from neo4j import GraphDatabase
    creds = load_credentials(None)
    if not creds.get("NEO4J_URI"):
        raise ValueError("Neo4j credentials are missing. Configure the pipeline environment first.")
    driver = GraphDatabase.driver(creds["NEO4J_URI"], auth=(creds["NEO4J_USERNAME"], creds["NEO4J_PASSWORD"]),
                                 connection_timeout=10)
    return driver, creds.get("NEO4J_DATABASE") or "neo4j"


def check_load(session, nodes, prefix, name, registry):
    problems = registry.check(name, prefix, session)
    if problems:
        raise ValueError("; ".join(problems))
    keys = list(nodes)
    for offset in range(0, len(keys), 500):
        clashes = list(session.run(
            "MATCH (n) WHERE n.key IN $keys AND "
            "(size(labels(n)) = 0 OR any(l IN labels(n) WHERE NOT l STARTS WITH $prefix)) "
            "RETURN n.key AS key, labels(n) AS labels LIMIT 8",
            keys=keys[offset:offset + 500], prefix=prefix))
        if clashes:
            raise ValueError("Load blocked: the pipeline matches relationship endpoints by key without labels. "
                             "Keys also exist outside the selected prefix: " + ", ".join(str(row["key"]) for row in clashes) +
                             ". CSVs remain available. The loader needs a separately approved pipeline fix.")
        if list(session.run("MATCH (n) WHERE n.key IN $keys WITH n.key AS key, count(n) AS c "
                            "WHERE c > 1 RETURN key LIMIT 1", keys=keys[offset:offset + 500])):
            raise ValueError("Load blocked: duplicate graph keys make relationship endpoints ambiguous.")


def build(data, result=None):
    if result is None:
        result = {}
    import corpus_config as corpus
    import build_corpus as official
    import prep_corpus
    registry = registry_checks()
    problems = registry.check(corpus.NAME, corpus.NODE_PREFIX)
    if problems:
        raise ValueError("; ".join(problems))
    if not Path(corpus.PROFILE_PATH).is_file():
        raise ValueError("A structural profile is required. Add or induce one before building.")
    emit("validation", "Validating the entire source against its structural profile (free)")
    run([sys.executable, str(ROOT / "induce_structure.py"), corpus.RAW_PATH, "--profile", corpus.PROFILE_PATH])
    clauses = prep_corpus.provisions()
    only = data.get("onlyProvisions") or []
    if set(only) - set(clauses):
        raise ValueError("Unknown pilot provisions: " + ", ".join(sorted(set(only) - set(clauses))))
    model = data.get("extractionModel", "gpt-4o")
    def artifact_warnings():
        problems = []
        for pid in clauses:
            for suffix in (".verified.yaml", ".clean.yaml"):
                path = Path(corpus.SPIRES_OUTPUT_DIR) / corpus.provision_filename(pid, suffix)
                if path.exists():
                    stored = str(read_yaml(path).get("provision_id"))
                    if stored != pid:
                        problems.append(f"{path.name}: stored provision identifier {stored!r} differs from {pid!r}. "
                                        "Artifact reused without alteration; inspect it before loading.")
        return problems
    if data.get("estimateOnly"):
        command = [sys.executable, str(ROOT / "build_corpus.py"), "--dry-run", "--model", model]
        if only:
            command += ["--only", *only]
        run(command)
        return {"summary": "Free preflight passed. No extraction or generation was purchased.", "provisions": len(clauses),
                "warnings": ["Extraction estimates use built-in prices and measured recursion assumptions. "
                             "Profile induction, verification and scenario generation are separate costs. "
                             "If only cleaned/verified artifacts remain, the upstream estimate may overstate new work.",
                             *artifact_warnings()]}
    if os.environ.get("GRAPH_BACKEND", "local") == "local":
        (Path(corpus.SPIRES_GRAPH_DIR) / "ready.json").unlink(missing_ok=True)
    original_stage_build = official.stage_build
    official.stage_build = lambda _dry: original_stage_build(True)
    original_extract = official.stage_extract
    def extract(model, dry_run, selection):
        from corpora import read_yaml
        def reusable(pid):
            raw = Path(corpus.SPIRES_OUTPUT_DIR) / corpus.provision_filename(pid, ".yaml")
            if raw.exists() and isinstance(read_yaml(raw).get("extracted_object"), dict):
                return True
            for suffix in (".verified.yaml", ".clean.yaml"):
                path = Path(corpus.SPIRES_OUTPUT_DIR) / corpus.provision_filename(pid, suffix)
                if path.exists():
                    value = read_yaml(path)
                    if value.get("provision_id") and isinstance(value.get("norms"), list):
                        return True
            return False
        missing = [pid for pid in selection or clauses if not reusable(pid)]
        emit("extract", f"{len(selection or clauses) - len(missing)} provisions have reusable extraction artifacts")
        if missing:
            original_extract(model, dry_run, missing)
        # Upstream returns 0 after some individual failures: prevent silent partial graphs.
        import yaml
        failed = []
        for pid in selection or clauses:
            if reusable(pid):
                continue
            raw = Path(corpus.SPIRES_OUTPUT_DIR) / corpus.provision_filename(pid, ".yaml")
            try:
                value = yaml.safe_load(raw.read_text(encoding="utf-8")) or {}
                if not isinstance(value.get("extracted_object"), dict):
                    failed.append(pid)
            except Exception:
                failed.append(pid)
        if failed:
            raise ValueError("Extraction incomplete or invalid for: " + ", ".join(failed) + ". Inspect errors.log before retrying.")
    official.stage_extract = extract
    original_clean = official.stage_clean
    def clean(dry_run):
        original_clean(dry_run)
        # Verified outputs already have the official cleaned shape; permit the
        # modality gate to inspect them even when their original raw file is absent.
        for pid in clauses:
            cleaned = Path(corpus.SPIRES_OUTPUT_DIR) / corpus.provision_filename(pid, ".clean.yaml")
            verified = Path(corpus.SPIRES_OUTPUT_DIR) / corpus.provision_filename(pid, ".verified.yaml")
            if not cleaned.exists() and verified.exists():
                shutil.copy2(verified, cleaned)
    official.stage_clean = clean
    original_run = official._run
    def stage_run(stage, argv):
        if stage != "verify":
            return original_run(stage, argv)
        from spires.clean_extraction import norm_sentences
        from verification import verify_missing
        entries = verify_missing(clauses, corpus, argv, emit, norm_sentences)
        result["verification"] = entries
        result.setdefault("warnings", []).extend(
            f"{entry['provision']}: {entry['message']}" for entry in entries
            if entry["status"] in ("skipped", "failed"))
    official._run = stage_run
    sys.argv = [str(ROOT / "build_corpus.py"), "--model", model]
    if only:
        sys.argv += ["--only", *only]
    if not data.get("verifyExceptions", True):
        sys.argv += ["--skip-verify"]
    if official.main():
        raise RuntimeError("The official build stopped at a validation gate. See the log.")
    result.update({"summary": "Graph CSVs built", "loaded": False, "provisions": len(clauses),
              "warnings": result.get("warnings", []) + (["A pilot limits new extraction; the graph includes all available artifacts for this corpus."] if only else []) + artifact_warnings()})
    if os.environ.get("GRAPH_BACKEND", "local") == "local":
        problems = []
        if any(entry["status"] == "failed" for entry in result.get("verification", [])):
            problems.append("Local graph activation is blocked by verification failures.")
        if artifact_warnings():
            problems.append("Local graph activation is blocked by artifact identifier mismatches.")
        if problems:
            result["warnings"].extend(problems)
        else:
            graph_dir = Path(corpus.SPIRES_GRAPH_DIR)
            (graph_dir / "ready.json").write_text(json.dumps({"corpus": corpus.NAME}), encoding="utf-8")
            result.update(loaded=True, summary="Selected corpus graph is ready in local storage")
    elif data.get("loadGraph"):
        if any(entry["status"] == "failed" for entry in result.get("verification", [])):
            raise ValueError("CSV build completed, but loading is blocked by verification failures. See the per-provision report and retry after fixing the cause.")
        if artifact_warnings():
            raise ValueError("Loading blocked by artifact identifier mismatches. Run a free preflight for the file names and details. "
                             "CSV output is available; source artifacts have not been changed.")
        emit("load", "Checking registry and cross-prefix endpoint keys before loading")
        from spires import build_spires_graph as graph
        documents = graph.load_provisions()
        nodes, edges, counts = graph.build(documents, clauses)
        graph.add_reference_edges(nodes, edges, counts, documents)
        driver, database = connection()
        try:
            with driver.session(database=database) as session:
                check_load(session, nodes, corpus.NODE_PREFIX, corpus.NAME, registry)
        finally:
            driver.close()
        if data.get("wipe") and data.get("confirmPrefix") != corpus.NODE_PREFIX:
            raise ValueError("Replacement requires the exact selected corpus prefix")
        graph.load(nodes, edges, bool(data.get("wipe")), None)
        result.update(loaded=True, summary="Selected corpus loaded into Neo4j")
    return result


def targets(data):
    from kg_testgen.cli import _provision_text
    selected = data.get("provisions") or []
    if selected:
        return [(pid, _provision_text(pid)) for pid in dict.fromkeys(selected)]
    text = str(data.get("requirement", "")).strip()
    if not text:
        raise ValueError("Select provisions or enter clause text")
    return [(data.get("provisionId") or None, text)]


def generation_command(data, pid, run_dir, input_path=None):
    import corpus_config
    command = [sys.executable, "-m", "kg_testgen", "--corpus", os.environ["CORPUS_CONFIG"],
               "--context-mode", "spires", "--node-label", corpus_config.label("Provision"), "--output-dir", str(run_dir)]
    if input_path:
        command += [str(input_path)]
        if pid:
            command += ["--provision-id", pid]
    else:
        command += ["--provision", pid]
    for key, flag in {"llmModel": "--llm-model", "temperature": "--temperature", "maxOutputTokens": "--max-output-tokens",
                      "spiresHopDepth": "--spires-hop-depth", "spiresModalityFilter": "--spires-modality-filter"}.items():
        if key in data and data[key] != "":
            command += [flag, str(data[key])]
    for key, flag, expected in [("spiresInbound", "--no-spires-inbound", False),
                                 ("spiresExceptionsFirst", "--no-spires-exceptions-first", False),
                                 ("spiresProvisionText", "--spires-provision-text", True),
                                 ("allowUnaugmented", "--allow-unaugmented", True)]:
        if data.get(key) is expected:
            command.append(flag)
    return command


def register(data, directory):
    import yaml
    name = str(data.get("name", "")).strip()
    if not name or not str(data.get("text", "")).strip():
        raise ValueError("Corpus name and source text are required")
    # Reject ownership conflicts before asking the model to induce a profile.
    if not data.get("estimateOnly"):
        registry = registry_checks()
        problems = registry.check(name, str(data.get("nodePrefix", "")))
        if problems:
            raise ValueError("; ".join(problems))
        if any(x.get("nodePrefix") == data.get("nodePrefix") or x["name"] == name for x in discover()):
            raise ValueError("Corpus name or node prefix is already configured")
    source = directory / "source.txt"
    source.write_text(data["text"], encoding="utf-8")
    profile = directory / "profile.yaml"
    command = [sys.executable, str(ROOT / "induce_structure.py"), str(source)]
    if data.get("profileText", "").strip():
        profile.write_text(data["profileText"], encoding="utf-8")
        command += ["--profile", str(profile)]
    elif data.get("estimateOnly"):
        run([*command, "--dry-run"])
        return {"summary": "Profile induction estimate only. Full structural validation needs a profile.", "warnings": []}
    else:
        command += ["--out", str(profile), "--model", data.get("extractionModel", "gpt-4o")]
    candidate = directory / "corpus.yaml"
    run([*command, "--write-config", str(candidate)])
    cfg = read_yaml(candidate)
    cfg["name"] = name
    cfg["description"] = data.get("description") or name
    cfg["node_prefix"] = data.get("nodePrefix") or cfg["node_prefix"]
    cfg["file_prefix"] = "ui_" + directory.name.replace("-", "_")
    cfg["paths"].update(schema=str(ROOT / "spires/norm.yaml"), spires_dir=str(directory / "spires"),
                         raw=str(source), profile=str(profile))
    candidate.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    from corpora import strict_config
    strict_config(candidate)
    if any(x.get("nodePrefix") == cfg["node_prefix"] or x["name"] == name for x in discover()):
        raise ValueError("Corpus name or node prefix is already configured. Choose unique values.")
    destination = DATA / "corpora" / directory.name
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "corpus.yaml").write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
    return {"summary": "Corpus saved with a validated profile. Run free preflight before extraction.",
            "profile": profile.read_text(encoding="utf-8"), "warnings": []}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    request = Path(args.request)
    data = json.loads(request.read_text(encoding="utf-8"))
    directory = request.parent
    sys.path.insert(0, str(ROOT))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    os.environ["PYTHONUNBUFFERED"] = "1"
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    kind = data["kind"]
    backend = data.get("graphBackend", "local")
    if backend not in ("local", "neo4j"):
        raise ValueError("Unknown graph backend")
    os.environ["GRAPH_BACKEND"] = backend
    result = {}
    try:
        if kind in ("review-preview", "review"):
            from policy_review import preview, run_review
            result = preview(data) if kind == "review-preview" else run_review(data, directory, emit)
        elif kind == "register":
            result = register(data, directory)
        else:
            if kind == "build":
                item, cfg, config = prepare(data["corpusId"])
            else:
                item, cfg = resolve(data["corpusId"])
                config = Path(item["configPath"])
            os.environ["CORPUS_CONFIG"] = str(config)
            if backend == "local":
                local_graph = workspace(item["id"]) / "spires/graph"
                if not (local_graph / "ready.json").exists():
                    local_graph = ROOT.parent / "seed_graphs" / cfg["name"]
                os.environ["LOCAL_GRAPH_DIR"] = str(local_graph)
            emit("corpus", f"{cfg['name']} • {cfg['node_prefix']} • CORPUS_CONFIG={config}")
            if kind == "build":
                started = time.time()
                try:
                    result = build(data, result)
                finally:
                    graph_dir = workspace(item["id"]) / "spires/graph"
                    for filename in ("nodes.csv", "edges.csv"):
                        path = graph_dir / filename
                        if path.exists() and path.stat().st_mtime >= started:
                            shutil.copy2(path, directory / filename)
                    errors = workspace(item["id"]) / "spires/output/errors.log"
                    if errors.exists():
                        shutil.copy2(errors, directory / "extraction-errors.log")
                    verification_report = workspace(item["id"]) / "spires/output/verification-report.json"
                    if verification_report.exists() and verification_report.stat().st_mtime >= started:
                        shutil.copy2(verification_report, directory / "verification-report.json")
            elif kind == "provisions":
                command = [sys.executable, "-m", "kg_testgen", "--corpus", str(config), "--list-provisions"]
                value = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
                if value.returncode:
                    raise ValueError(value.stderr or value.stdout)
                result = {"provisions": value.stdout.splitlines()}
            elif kind == "graph-status":
                if backend == "local":
                    from kg_testgen.local_graph import LocalGraphClient
                    with LocalGraphClient() as graph:
                        count = len(graph.provisions)
                    result = {"summary": f"Local graph ready: {count} provisions", "provisions": count,
                              "warnings": []}
                else:
                    registry = registry_checks()
                    driver, database = connection()
                    try:
                        with driver.session(database=database) as session:
                            count = session.run("MATCH (n) WHERE $label IN labels(n) RETURN count(n) AS c",
                                                label=cfg["node_prefix"] + "Provision").single()["c"]
                            result = {"summary": f"Connected: {count} provisions loaded", "provisions": count,
                                      "warnings": registry.check(cfg["name"], cfg["node_prefix"], session)}
                    finally:
                        driver.close()
            else:
                from query_preview import config_from_request, preview
                config_from_request(data)
                selected = targets(data)
                outputs = []
                for index, (pid, text) in enumerate(selected, 1):
                    emit("provision", f"{index}/{len(selected)} {pid or 'manual clause'}")
                    if kind == "preview":
                        outputs.append(preview(data, pid, text))
                    elif kind == "generate":
                        run_dir = directory / "runs" / f"{index:03d}"
                        input_path = None
                        if not data.get("provisions"):
                            input_path = directory / "requirement.txt"
                            input_path.write_text(text, encoding="utf-8")
                        try:
                            run(generation_command(data, pid, run_dir, input_path))
                        except RuntimeError as exc:
                            outputs.append({"provision": pid, "error": str(exc)})
                            continue
                        outputs.append({"provision": pid, "summary": "Generated"})
                    else:
                        raise ValueError("Unknown operation")
                result = {"items": outputs, "summary": f"Processed {len(outputs)} provision(s)"}
                if any(x.get("error") for x in outputs):
                    result["error"] = "Some provisions failed; successful outputs remain available."
        Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1 if result.get("error") else 0
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if "Configured node label" in message and "not present" in message:
            message = ("The selected corpus graph is unavailable. Check the selected graph storage and build or import its graph.\n\n" + message)
        if kind == "review" and Path(args.result).exists():
            result = json.loads(Path(args.result).read_text(encoding="utf-8"))
        result.update(error=message)
        Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        emit("error", result["error"])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
