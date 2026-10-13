"""Public, workspace-scoped compute API.

The browser stores the durable workspace ZIP in IndexedDB. Server files are
temporary working copies that can be reconstructed by uploading that ZIP.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from backend import server as core


ROOT = core.UI_ROOT
PIPELINE = core.PIPELINE_ROOT
SCRATCH = Path(os.environ.get("PIPELINE_UI_SCRATCH") or Path(tempfile.gettempdir()) / "normgraph-visitors").resolve()
HOST = os.environ.get("PIPELINE_UI_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT") or os.environ.get("PIPELINE_UI_PORT", "8765"))
MAX_SNAPSHOT = 30 * 1024 * 1024
ALLOWED_SUFFIXES = {".yaml", ".json", ".csv", ".txt", ".log", ".feature"}
STATES = {}
STATES_LOCK = threading.RLock()


def _python():
    for path in (ROOT.parent / ".venv-testgen/Scripts/python.exe", ROOT.parent / ".venv-testgen/bin/python"):
        if path.exists():
            return str(path)
    return sys.executable


def _workspace(token):
    if not re.fullmatch(r"[a-f0-9]{64}", token or ""):
        raise ValueError("Open a browser workspace first")
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    with STATES_LOCK:
        if digest not in STATES:
            STATES[digest] = Workspace(SCRATCH / digest)
        return STATES[digest]


class Workspace:
    def __init__(self, path):
        self.path = path
        self.data = path / "data"
        self.jobs_dir = self.data / "jobs"
        self.jobs = {}
        self.processes = {}
        self.transient_secrets = {}
        self.lock = threading.RLock()
        self.present = path.exists()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        for file in self.jobs_dir.glob("*/job.json"):
            job = core._read_json(file, {})
            if job.get("id") != file.parent.name:
                continue
            if job.get("status") in core.ACTIVE:
                job.update(status="interrupted", finishedAt=time.time(),
                           error="The worker restarted. Restore the last browser snapshot and retry.")
                self.save(job)
            job["result"] = core._result(file.parent)
            self.jobs[job["id"]] = job

    def env(self, supplied=None):
        env = {key: value for key, value in os.environ.items()
               if key not in {"OPENAI_API_KEY", "OPENAI_KEY", "NEO4J_URI", "NEO4J_USERNAME",
                              "NEO4J_PASSWORD", "NEO4J_DATABASE"}}
        env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1",
                   PIPELINE_UI_DATA_DIR=str(self.data))
        env.update(supplied or {})
        if env.get("OPENAI_API_KEY"):
            env["OPENAI_KEY"] = env["OPENAI_API_KEY"]
        return env

    def corpora(self):
        result = subprocess.run([_python(), "-B", str(ROOT / "backend/tools/corpora.py")],
                                cwd=PIPELINE, env=self.env(), capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        if result.returncode:
            raise ValueError("Corpus discovery failed: " + result.stderr[-1000:])
        return json.loads(result.stdout)

    def save(self, job):
        core._write_json(self.jobs_dir / job["id"] / "job.json", job)

    def redact_bytes(self, payload):
        for values in self.transient_secrets.values():
            for secret in values:
                payload = payload.replace(secret.encode("utf-8"), b"[redacted]")
        return payload

    @staticmethod
    def redact_value(value, secrets):
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, dict):
            return {key: Workspace.redact_value(item, secrets) for key, item in value.items()}
        if isinstance(value, list):
            return [Workspace.redact_value(item, secrets) for item in value]
        return value

    def start(self, kind, data, supplied):
        core._validate_request(kind, data)
        backend = data.get("graphBackend", "local")
        if backend not in ("local", "neo4j"):
            raise ValueError("Select local or Neo4j graph storage")
        paid = (kind in {"generate", "build"} or
                kind == "register" and not data.get("estimateOnly") and not str(data.get("profileText", "")).strip())
        if paid and not supplied.get("OPENAI_API_KEY"):
            raise ValueError("Enter your OpenAI API key before this operation")
        needs_neo4j = kind in {"preview", "generate", "graph-status"} or (kind == "build" and data.get("loadGraph"))
        if backend == "neo4j" and needs_neo4j and not all(
                supplied.get(key) for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")):
            raise ValueError("Enter your Neo4j connection details for this operation")
        item = None
        if kind != "register":
            item = next((x for x in self.corpora() if x["id"] == data.get("corpusId")), None)
            if not item or item.get("errors"):
                raise ValueError("Unknown or invalid corpus")
            if kind == "build" and data.get("wipe") and data.get("confirmPrefix") != item["nodePrefix"]:
                raise ValueError("Type the selected corpus prefix to confirm replacement")
        with STATES_LOCK, self.lock:
            active = sum(job["status"] in core.ACTIVE for job in self.jobs.values())
            if active >= 2:
                raise ValueError("Two jobs are already active in this workspace")
            global_active = sum(job["status"] in core.ACTIVE
                                for state in STATES.values() for job in state.jobs.values())
            if global_active >= 4:
                raise ValueError("The public worker is busy. Retry after another job finishes")
            if kind in {"build", "register"} and any(j["kind"] in {"build", "register"}
                                                      and j["status"] in core.ACTIVE for j in self.jobs.values()):
                raise ValueError("Another build or corpus registration is active")
            fingerprint = core._fingerprint(item, data) if item else None
            if kind == "build" and not data.get("estimateOnly"):
                preflight = self.jobs.get(data.get("preflightId"))
                if not preflight or preflight["kind"] != "build" or preflight["status"] != "succeeded" or not preflight["request"].get("estimateOnly") or preflight.get("fingerprint") != fingerprint:
                    raise ValueError("Run a matching preflight before building")
            jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            directory = self.jobs_dir / jid
            directory.mkdir(parents=True)
            request = {**data, "kind": kind}
            core._write_json(directory / "request.json", request)
            command = [_python(), "-B", str(ROOT / "backend/tools/workflow.py"),
                       "--request", str(directory / "request.json"), "--result", str(directory / "result.json")]
            job = {"id": jid, "kind": kind,
                   "label": f"{kind} · {(item or {}).get('name', data.get('name', 'New corpus'))}",
                   "status": "queued", "createdAt": time.time(), "logs": [],
                   "request": request, "command": subprocess.list2cmdline(command),
                   "fingerprint": fingerprint, "stage": "queued"}
            self.save(job)
            self.jobs[jid] = job
            self.transient_secrets[jid] = [supplied[key] for key in ("OPENAI_API_KEY", "NEO4J_PASSWORD")
                                           if supplied.get(key)]
            threading.Thread(target=self.worker, args=(job, command, supplied), daemon=True).start()
            return dict(job)

    def worker(self, job, command, supplied):
        directory = self.jobs_dir / job["id"]
        with self.lock:
            if job["status"] == "cancelled":
                self.transient_secrets.pop(job["id"], None)
                return
            job.update(status="running", startedAt=time.time())
            self.save(job)
        try:
            process = subprocess.Popen(command, cwd=PIPELINE, env=self.env(supplied),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace",
                                       start_new_session=os.name != "nt")
            with self.lock:
                self.processes[job["id"]] = process
            with (directory / "output.log").open("a", encoding="utf-8") as log:
                for line in process.stdout:
                    # Provider errors must not echo credentials into browser history.
                    for secret in supplied.values():
                        if secret:
                            line = line.replace(secret, "[redacted]")
                    log.write(line)
                    with self.lock:
                        job["logs"] = [*job["logs"][-499:], line.rstrip()]
                        stage = re.match(r"\[([a-z-]+)\]", line)
                        if stage and stage.group(1) != "command":
                            job["stage"] = stage.group(1)
                        progress = re.search(r"\[\s*(\d+)\s*/\s*(\d+)\]", line)
                        if progress:
                            job["progress"] = {"done": int(progress[1]), "total": int(progress[2])}
                        self.save(job)
            process.stdout.close()
            code = process.wait()
            with self.lock:
                job["returnCode"] = code
                job["status"] = "cancelled" if job["status"] == "cancelling" else "succeeded" if code == 0 else "failed"
        except Exception as exc:
            with self.lock:
                job.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            with self.lock:
                self.processes.pop(job["id"], None)
                job.update(finishedAt=time.time(), result=core._result(directory))
                if job["result"].get("error"):
                    job["error"] = job["result"]["error"]
                secrets = self.transient_secrets.get(job["id"], [])
                job.update(self.redact_value(job, secrets))
                self.save(job)
                for path in self.data.rglob("*"):
                    if path.is_file() and path.suffix in ALLOWED_SUFFIXES:
                        try:
                            original = path.read_bytes()
                            redacted = self.redact_bytes(original)
                            if redacted != original:
                                path.write_bytes(redacted)
                        except OSError:
                            continue
                self.transient_secrets.pop(job["id"], None)

    def cancel(self, jid):
        with self.lock:
            job = self.jobs.get(jid)
            if not job:
                raise ValueError("Job not found")
            process = self.processes.get(jid)
            if not process and job["status"] in core.ACTIVE:
                job.update(status="cancelled", finishedAt=time.time())
            elif process and job["status"] in core.ACTIVE:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                job["status"] = "cancelling"
            self.save(job)
            return dict(job)

    def snapshot(self):
        buffer = io.BytesIO()
        with self.lock, zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in self.data.rglob("*"):
                if path.is_file() and path.suffix in ALLOWED_SUFFIXES:
                    try:
                        archive.writestr(path.relative_to(self.data).as_posix(),
                                         self.redact_bytes(path.read_bytes()))
                    except OSError:
                        continue  # A running process may be replacing this file.
        return buffer.getvalue()

    def restore(self, raw):
        if len(raw) > MAX_SNAPSHOT:
            raise ValueError("Workspace snapshot exceeds 30 MB")
        with self.lock:
            if any(j["status"] in core.ACTIVE for j in self.jobs.values()):
                raise ValueError("Wait for active jobs before restoring a snapshot")
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                if len(members) > 10000 or sum(m.file_size for m in members) > 150 * 1024 * 1024:
                    raise ValueError("Workspace snapshot is too large")
                for member in members:
                    relative = Path(member.filename)
                    if (member.is_dir() or not relative.parts or relative.is_absolute() or ".." in relative.parts
                            or "\\" in member.filename or ":" in member.filename
                            or relative.suffix not in ALLOWED_SUFFIXES
                            or relative.parts[0] not in {"jobs", "workspaces", "corpora", "registry.yaml"}):
                        raise ValueError("Snapshot contains an invalid path")
                files = {member.filename: archive.read(member) for member in members}
            if self.data.resolve().parent != self.path.resolve() or not self.path.is_relative_to(SCRATCH):
                raise ValueError("Unsafe workspace location")
            shutil.rmtree(self.data)
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            for name, contents in files.items():
                target = (self.data / name).resolve()
                if not target.is_relative_to(self.data):
                    raise ValueError("Snapshot path escapes this workspace")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(contents)
            self._rewrite_restored_configs()
            self.jobs.clear()
            for file in self.jobs_dir.glob("*/job.json"):
                job = core._read_json(file, {})
                if job.get("id") == file.parent.name:
                    job["result"] = core._result(file.parent)
                    self.jobs[job["id"]] = job
            self.present = True

    def _rewrite_restored_configs(self):
        import yaml

        schema = str(PIPELINE / "spires/norm.yaml")
        for path in (self.data / "corpora").glob("*/corpus.yaml"):
            job_dir = self.jobs_dir / path.parent.name
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            config["paths"].update(raw=str(job_dir / "source.txt"),
                                   profile=str(job_dir / "profile.yaml"),
                                   spires_dir=str(job_dir / "spires"), schema=schema)
            path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        for path in (self.data / "workspaces").glob("*/corpus.yaml"):
            directory = path.parent
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            config["paths"].update(raw=str(directory / "source.txt"),
                                   profile=str(directory / "profile.yaml"),
                                   spires_dir=str(directory / "spires"), schema=schema)
            path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            (directory / "source-fingerprint.txt").unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.netloc == self.headers.get("Host") or origin == "http://localhost:3000"

    def _send(self, value, status=200, *, raw=False, cookie=None):
        payload = value if raw else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if self.headers.get("Origin") == "http://localhost:3000":
            self.send_header("Access-Control-Allow-Origin", "http://localhost:3000")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-OpenAI-Key,X-Neo4j-URI,X-Neo4j-Username,X-Neo4j-Password,X-Neo4j-Database")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,OPTIONS")
        self.send_header("Content-Type", "application/zip" if raw else "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _workspace(self):
        jar = cookies.SimpleCookie()
        jar.load(self.headers.get("Cookie", ""))
        return _workspace(jar.get("normgraph_workspace").value if jar.get("normgraph_workspace") else "")

    def do_OPTIONS(self):
        self._send({}, 200 if self._origin_ok() else 403)

    def do_GET(self):
        try:
            path = unquote(urlparse(self.path).path)
            if path == "/api/status":
                self._send({"api": "ready", "testgenPython": _python() != sys.executable,
                            "ontoGPT": bool(os.environ.get("ONTOGPT"))})
                return
            workspace = self._workspace()
            if not workspace.present:
                self._send({"error": "Workspace worker restarted; reopen and restore your browser snapshot"}, 409)
                return
            if path == "/api/corpora":
                self._send(workspace.corpora())
            elif path == "/api/jobs":
                with workspace.lock:
                    self._send(sorted(workspace.jobs.values(), key=lambda x: x["createdAt"], reverse=True))
            elif path == "/api/workspace/snapshot":
                self._send(workspace.snapshot(), raw=True)
            elif path.startswith("/api/jobs/") and "/files/" in path:
                jid, filename = path[len("/api/jobs/"):].split("/files/", 1)
                base = (workspace.jobs_dir / jid).resolve()
                file = (base / filename).resolve()
                if base.parent != workspace.jobs_dir or not file.is_relative_to(base) or not file.is_file() or file.suffix not in ALLOWED_SUFFIXES:
                    self._send({"error": "File not found"}, 404)
                    return
                with workspace.lock:
                    payload = workspace.redact_bytes(file.read_bytes())
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif path.startswith("/api/jobs/"):
                with workspace.lock:
                    job = workspace.jobs.get(path.rsplit("/", 1)[-1])
                    self._send(job or {"error": "Job not found"}, 200 if job else 404)
            else:
                self._send({"error": "Not found"}, 404)
        except (ValueError, zipfile.BadZipFile) as exc:
            self._send({"error": str(exc)}, 400)
        except Exception as exc:
            self._send({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self):
        try:
            if not self._origin_ok() or not self.headers.get("Content-Type", "").startswith("application/json"):
                self._send({"error": "Invalid request origin or content type"}, 403)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 12 * 1024 * 1024:
                raise ValueError("JSON request exceeds 12 MB")
            body = json.loads(self.rfile.read(size) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/workspace/open":
                token = body.get("token", "")
                workspace = _workspace(token)
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
                cookie = f"normgraph_workspace={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000{secure}"
                self._send({"hasData": workspace.present}, cookie=cookie)
                workspace.present = True
                return
            workspace = self._workspace()
            if not workspace.present:
                self._send({"error": "Workspace worker restarted; reopen and restore your browser snapshot"}, 409)
                return
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self._send(workspace.cancel(path.split("/")[-2]))
            elif path.startswith("/api/jobs/"):
                kind = path.rsplit("/", 1)[-1]
                supplied = {"OPENAI_API_KEY": self.headers.get("X-OpenAI-Key", ""),
                            "NEO4J_URI": self.headers.get("X-Neo4j-URI", ""),
                            "NEO4J_USERNAME": self.headers.get("X-Neo4j-Username", ""),
                            "NEO4J_PASSWORD": self.headers.get("X-Neo4j-Password", ""),
                            "NEO4J_DATABASE": self.headers.get("X-Neo4j-Database", "")}
                self._send(workspace.start("preview" if kind == "query-preview" else kind, body, supplied), 202)
            else:
                self._send({"error": "Not found"}, 404)
        except (ValueError, KeyError, TypeError) as exc:
            self._send({"error": str(exc)}, 400)
        except Exception as exc:
            self._send({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_PUT(self):
        try:
            if not self._origin_ok() or self.path != "/api/workspace/snapshot" or self.headers.get("Content-Type") != "application/zip":
                self._send({"error": "Invalid snapshot request"}, 403)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > MAX_SNAPSHOT:
                raise ValueError("Workspace snapshot exceeds 30 MB")
            workspace = self._workspace()
            workspace.restore(self.rfile.read(size))
            self._send({"restored": True})
        except (ValueError, zipfile.BadZipFile) as exc:
            self._send({"error": str(exc)}, 400)
        except Exception as exc:
            self._send({"error": f"{type(exc).__name__}: {exc}"}, 500)


if __name__ == "__main__":
    SCRATCH.mkdir(parents=True, exist_ok=True)
    print(f"NormGraph compute API listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
