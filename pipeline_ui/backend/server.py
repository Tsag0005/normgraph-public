"""Local HTTP boundary. Pipeline work runs in a separate, corpus-configured process."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

UI_ROOT = Path(__file__).resolve().parents[1]
# Two roots, because the split separated them: the virtualenvs and the .env sit at the
# repository root, while the pipeline's scripts and corpus configs are in pipeline/ and
# every command is run from there.
REPO_ROOT = UI_ROOT.parent
PIPELINE_ROOT = REPO_ROOT / "pipeline"
DATA_ROOT = UI_ROOT / "data"
JOB_ROOT = DATA_ROOT / "jobs"
HOST = os.environ.get("PIPELINE_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("PIPELINE_UI_PORT", "8765"))
LOCK = threading.RLock()
JOBS = {}
PROCESSES = {}
ACTIVE = {"queued", "running", "cancelling"}


def _python():
    for path in (REPO_ROOT / ".venv-testgen/Scripts/python.exe", REPO_ROOT / ".venv-testgen/bin/python"):
        if path.exists():
            return str(path)
    return sys.executable


def _env():
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def _read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.stem}-{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                temp.replace(path)
                break
            except OSError as exc:
                if attempt == 4 or not (isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33)):
                    raise
                time.sleep(0.025 * 2 ** attempt)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _corpora():
    result = subprocess.run([_python(), "-B", str(UI_ROOT / "backend/tools/corpora.py")],
                            cwd=PIPELINE_ROOT, env=_env(), capture_output=True, text=True, encoding="utf-8", timeout=30)
    if result.returncode:
        raise ValueError("Corpus discovery failed. The pipeline Python needs PyYAML. " + result.stderr[-1000:])
    return json.loads(result.stdout)


def _context_from_prompt(prompt):
    if not prompt:
        return ""
    match = re.search(r"BLOCK 3 of \d+[^\n]*\n.*?\n={10,}\n(.*?)(?=\n={10,}\n(?:OUTPUT FORMAT|BLOCK 4)|\Z)", prompt, re.S)
    return match.group(1).strip() if match else ""


def _run_payload(directory):
    record = _read_json(directory / "run_record.json", {}) or {}
    error = record.get("error")
    if error and "Configured node label" in error and "not present" in error:
        error = ("The selected corpus graph is unavailable. Check the selected graph storage and build or import its graph before generating.\n\n" + error)
    prompt = (directory / "prompt.txt").read_text(encoding="utf-8") if (directory / "prompt.txt").exists() else record.get("prompt", "")
    warnings = list(record.get("gherkin_structure_problems") or [])
    if record.get("error"):
        warnings.append(record["error"])
    if (record.get("llm") or {}).get("finish_reason") == "length":
        warnings.append("The model reached its output-token limit. Scenarios may be truncated.")
    feature = (directory / "generated.feature").read_text(encoding="utf-8") if (directory / "generated.feature").exists() else record.get("gherkin") or ""
    return {"provision": (record.get("config") or {}).get("provision_id"), "feature": feature,
            "queryContext": _context_from_prompt(prompt), "prompt": prompt, "record": record,
            "details": record.get("spires_context") or {}, "usage": record.get("llm") or {},
            "warnings": warnings,
            "error": error if record.get("status") == "failed" else None}


def _result(directory):
    result = _read_json(directory / "result.json", {}) or {}
    runs = sorted(directory.glob("runs/*/run_record.json"))
    if (directory / "run/run_record.json").exists():
        runs.append(directory / "run/run_record.json")
    if runs:
        result["runs"] = [_run_payload(path.parent) for path in runs]
    files = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix in (".feature", ".csv", ".json", ".txt", ".yaml", ".log") and path.name != "job.json":
            files.append(str(path.relative_to(directory)).replace("\\", "/"))
    result["files"] = files
    return result


def _save(job, strict=False):
    # A history write must never stop draining a paid subprocess's output.
    snapshot = {key: value for key, value in job.items() if key != "persistenceWarning"}
    try:
        _write_json(JOB_ROOT / job["id"] / "job.json", snapshot)
    except OSError as exc:
        if strict:
            raise
        job["persistenceWarning"] = (
            "Could not save job history to disk. Live status remains available; "
            "saving will be retried. Keep the API running. " + str(exc))
    else:
        job.pop("persistenceWarning", None)


def _restore():
    for path in JOB_ROOT.glob("*/job.json"):
        job = _read_json(path, {})
        if not job or job.get("id") != path.parent.name:
            continue
        if job.get("status") in ACTIVE:
            job.update(status="interrupted", error="API restarted before completion was recorded. Check logs and artifacts before retrying.")
            job["finishedAt"] = time.time()
            _save(job)
        job["result"] = _result(path.parent)
        JOBS[job["id"]] = job
    # Keep pre-update generated runs available, without inventing a completed job state.
    for path in JOB_ROOT.glob("*/run/run_record.json"):
        jid = path.parent.parent.name
        if jid not in JOBS:
            JOBS[jid] = {"id": jid, "kind": "generate", "label": "Imported earlier run", "status": "imported",
                         "createdAt": path.stat().st_mtime, "logs": [], "result": _result(path.parent.parent), "request": {}}


def _fingerprint(item, data):
    digest = hashlib.sha256()
    for key in ("configPath", "path", "profile"):
        path = Path(item.get(key, ""))
        if path.is_file():
            digest.update(path.read_bytes())
    digest.update(json.dumps({k: data.get(k) for k in ("corpusId", "onlyProvisions", "extractionModel")}, sort_keys=True).encode())
    return digest.hexdigest()


def _validate_request(kind, data):
    if not isinstance(data, dict):
        raise ValueError("Request must be a JSON object")
    if kind not in {"build", "preview", "generate", "register", "provisions", "graph-status"}:
        raise ValueError("Unknown operation")
    if data.get("contextMode", "spires") != "spires":
        raise ValueError("Only SPIRES is available in production")
    for key in ("provisions", "onlyProvisions"):
        if key in data and (not isinstance(data[key], list) or len(data[key]) > 1000 or
                            any(not isinstance(x, str) or not x.strip() or len(x) > 200 for x in data[key])):
            raise ValueError(f"{key} must be a list of provision identifiers")
    for key in ("estimateOnly", "loadGraph", "wipe", "verifyExceptions", "spiresInbound", "spiresExceptionsFirst", "spiresProvisionText", "allowUnaugmented"):
        if key in data and not isinstance(data[key], bool):
            raise ValueError(f"{key} must be true or false")
    for key, low, high in (("spiresHopDepth", 1, 10), ("temperature", 0, 2), ("maxOutputTokens", 1, 128000)):
        value = data.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not low <= value <= high):
            raise ValueError(f"{key} must be between {low} and {high}")
        if value is not None and key != "temperature" and int(value) != value:
            raise ValueError(f"{key} must be a whole number")
    for key in ("llmModel", "extractionModel"):
        if key in data and (not isinstance(data[key], str) or not re.fullmatch(r"[\w./:-]{1,100}", data[key])):
            raise ValueError(f"Invalid {key}")
    if data.get("spiresModalityFilter", "") not in ("", "deontic"):
        raise ValueError("Choose all norms or deontic norms")
    if kind in ("generate", "preview") and not data.get("provisions") and not str(data.get("requirement", "")).strip():
        raise ValueError("Select provisions or enter a legislative clause")


def _worker(job, command):
    directory = JOB_ROOT / job["id"]
    with LOCK:
        if job["status"] == "cancelled":
            return
        job.update(status="running", startedAt=time.time())
        _save(job)
    try:
        with LOCK:
            if job["status"] == "cancelled":
                return
            process = subprocess.Popen(command, cwd=PIPELINE_ROOT, env=_env(), stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                       start_new_session=os.name != "nt")
            PROCESSES[job["id"]] = process
        with (directory / "output.log").open("a", encoding="utf-8") as log:
            last_save = time.monotonic()
            for line in process.stdout:
                log.write(line)
                log.flush()
                with LOCK:
                    job["logs"] = [*job["logs"][-499:], line.rstrip()]
                    stage = re.match(r"\[([a-z-]+)\]", line)
                    if stage and stage.group(1) != "command":
                        if job.get("stage") != stage.group(1):
                            job.pop("progress", None)
                        job["stage"] = stage.group(1)
                    progress = re.search(r"\[\s*(\d+)\s*/\s*(\d+)\]", line)
                    if not progress and stage and stage.group(1) == "provision":
                        progress = re.search(r"(\d+)/(\d+)", line)
                    if progress:
                        job["progress"] = {"done": int(progress[1]), "total": int(progress[2])}
                    if time.monotonic() - last_save >= 1:
                        _save(job)
                        last_save = time.monotonic()
        code = process.wait()
        with LOCK:
            job["returnCode"] = code
            job["status"] = "cancelled" if job["status"] == "cancelling" else "succeeded" if code == 0 else "failed"
    except Exception as exc:
        with LOCK:
            job.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        with LOCK:
            PROCESSES.pop(job["id"], None)
            job.update(finishedAt=time.time(), result=_result(directory))
            if job["result"].get("error"):
                job["error"] = job["result"]["error"]
            _save(job)


def _start_job(kind, data):
    _validate_request(kind, data)
    item = None
    if kind != "register":
        item = next((x for x in _corpora() if x["id"] == data.get("corpusId")), None)
        if not item or item.get("errors"):
            raise ValueError("Unknown or invalid corpus: " + "; ".join((item or {}).get("errors", [])))
        if kind == "build" and data.get("wipe") and data.get("confirmPrefix") != item["nodePrefix"]:
            raise ValueError("Type the selected corpus prefix to confirm replacement")
    with LOCK:
        # Serialize builds and registration, including ownership claims and artifact reuse.
        if kind in ("build", "register") and any(j["kind"] in ("build", "register") and j["status"] in ACTIVE for j in JOBS.values()):
            raise ValueError("Another build or corpus registration is active. Wait for it to finish.")
        fingerprint = _fingerprint(item, data) if item else None
        if kind == "build" and not data.get("estimateOnly"):
            preflight = JOBS.get(data.get("preflightId"))
            if not preflight or preflight["kind"] != "build" or preflight["status"] != "succeeded" or not preflight["request"].get("estimateOnly") or preflight.get("fingerprint") != fingerprint:
                raise ValueError("Run the free preflight for this corpus, model and pilot selection before building")
        jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        directory = JOB_ROOT / jid
        directory.mkdir(parents=True)
        request = {**data, "kind": kind}
        _write_json(directory / "request.json", request)
        command = [_python(), "-B", str(UI_ROOT / "backend/tools/workflow.py"), "--request", str(directory / "request.json"), "--result", str(directory / "result.json")]
        job = {"id": jid, "kind": kind, "label": f"{kind} · {(item or {}).get('name', data.get('name', 'New corpus'))}",
               "status": "queued", "createdAt": time.time(), "logs": [], "request": request,
               "command": subprocess.list2cmdline(command), "fingerprint": fingerprint, "stage": "queued"}
        _save(job, strict=True)
        JOBS[jid] = job
        threading.Thread(target=_worker, args=(job, command), daemon=True).start()
        return dict(job)


def _cancel(jid):
    with LOCK:
        job = JOBS.get(jid)
        process = PROCESSES.get(jid)
        if not job:
            raise ValueError("Job not found")
        if not process and job["status"] in ACTIVE:
            job.update(status="cancelled", finishedAt=time.time())
            _save(job)
            return dict(job)
        if not process or job["status"] not in ACTIVE:
            return dict(job)
        if os.name == "nt":
            result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
            if result.returncode and process.poll() is None:
                raise ValueError("Could not stop the process tree. Check the local terminal.")
        else:
            import signal
            os.killpg(process.pid, signal.SIGTERM)
        job["status"] = "cancelling"
        _save(job)
        return dict(job)


class Handler(BaseHTTPRequestHandler):
    def _origin_ok(self):
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).hostname in ("localhost", "127.0.0.1")

    def _send(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        origin = self.headers.get("Origin")
        if origin and self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._send({}, 200 if self._origin_ok() else 403)

    def do_GET(self):
        try:
            path = unquote(urlparse(self.path).path)
            if path == "/api/status":
                self._send({"api": "ready", "testgenPython": _python() != sys.executable,
                            "ontoGPT": (REPO_ROOT / ".venv-ontogpt/Scripts/ontogpt.exe").exists()})
            elif path == "/api/corpora":
                self._send(_corpora())
            elif path == "/api/jobs":
                with LOCK:
                    for job in JOBS.values():
                        if job.get("persistenceWarning"):
                            _save(job)
                    self._send(sorted(JOBS.values(), key=lambda x: x["createdAt"], reverse=True))
            elif path.startswith("/api/jobs/") and "/files/" in path:
                jid, filename = path[len("/api/jobs/"):].split("/files/", 1)
                base = (JOB_ROOT / jid).resolve()
                file = (base / filename).resolve()
                if base.parent != JOB_ROOT.resolve() or not file.is_relative_to(base) or not file.is_file() or file.suffix not in (".feature", ".csv", ".json", ".txt", ".yaml", ".log"):
                    self._send({"error": "File not found"}, 404)
                    return
                raw = file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif path.startswith("/api/jobs/"):
                with LOCK:
                    job = JOBS.get(path.rsplit("/", 1)[-1])
                    self._send(job or {"error": "Job not found"}, 200 if job else 404)
            else:
                self._send({"error": "Not found"}, 404)
        except Exception as exc:
            self._send({"error": str(exc)}, 400)

    def do_POST(self):
        try:
            if not self._origin_ok() or not self.headers.get("Content-Type", "").startswith("application/json"):
                self._send({"error": "Local JSON requests only"}, 403)
                return
            size = int(self.headers.get("Content-Length", 0))
            if not 0 <= size <= 12 * 1024 * 1024:
                raise ValueError("Maximum request size is 12 MB")
            body = json.loads(self.rfile.read(size) or b"{}")
            path = urlparse(self.path).path
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self._send(_cancel(path.split("/")[-2]))
            elif path.startswith("/api/jobs/"):
                kind = path.rsplit("/", 1)[-1]
                self._send(_start_job("preview" if kind == "query-preview" else kind, body), 202)
            else:
                self._send({"error": "Not found"}, 404)
        except (ValueError, KeyError, TypeError) as exc:
            self._send({"error": str(exc)}, 400)
        except Exception as exc:
            self._send({"error": f"{type(exc).__name__}: {exc}"}, 500)


if __name__ == "__main__":
    _restore()
    print(f"NormGraph local API: http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
