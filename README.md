# NormGraph Public Workbench

Public, self-hosted edition of the legal-norm graph and Gherkin generator. This is a
separate repository containing the production pipeline, the website, and two bundled
example graphs. Evaluation notebooks, reports, and previous site runtime files are
excluded.

## How it works

- Each browser keeps its corpus files, graph CSVs, job history, prompts, and generated
  features in IndexedDB. **Export workspace** makes a ZIP backup; **Import workspace**
  restores it in another browser. Clearing browser site data deletes its workspace.
- The Python API is a temporary compute worker. It restores a browser's ZIP when its
  working copy is missing. The default Docker deployment stores working copies on
  tmpfs, so a restart clears them.
- The visitor enters an OpenAI API key when starting a paid operation. The key stays
  in page memory and is passed to that job's process environment. It is never written
  into a workspace ZIP, job request, or repository. Preview and graph checks are free.
- The default graph backend reads the pipeline's SPIRES node and relationship CSVs.
  Schedule 1 and Part IIIC have bundled, immediately usable graphs. A visitor can
  optionally select Neo4j and enter their own URI, username, password, and database.
  Neo4j data lives in that visitor's Neo4j account, outside the browser backup.

The full flow remains available: discover built-in corpora, register and validate a
custom statute, induce its structural profile, run a free build preflight, extract
norms, verify exceptions, build a graph, preview context, and generate/download
Gherkin for one or multiple provisions. New extraction requires OntoGPT and the
visitor's OpenAI key. Pre-existing artifacts can be reused by the build.

## Local development

Requires Python 3.11 and Node.js 22. Create separate Python environments because
OntoGPT's dependencies conflict with the graph/test generator's dependencies.

```powershell
py -3.11 -m venv .venv-testgen
.\.venv-testgen\Scripts\python.exe -m pip install -r pipeline\requirements\pipeline.txt
py -3.11 -m venv .venv-ontogpt
.\.venv-ontogpt\Scripts\python.exe -m pip install -r pipeline\requirements\ontogpt.txt
cd pipeline_ui
npm ci
.\start.ps1
```

Open http://localhost:3000. The local development API listens on
http://localhost:8765. `pipeline_ui/stop.ps1` stops both processes.

The Docker deployment includes both Python environments:

```bash
docker compose up --build -d
```

For a public deployment, follow [the OCI Always Free guide](deploy/OCI.md).
You can use a free `PUBLIC_IP.sslip.io` hostname, so a purchased domain and a
DNS account are optional. Set `SITE_ADDRESS` to that hostname. Caddy obtains
HTTPS automatically when ports 80 and 443 are reachable. Do not ask visitors
to enter keys over HTTP.
No operator OpenAI or Neo4j credentials belong in the container environment.

An OCI Always Free Ampere A1 VM is a practical target for the Python extraction
stack, subject to account eligibility and capacity. Build on the VM with Docker
Compose. Do not assume a static-site host can run the Python pipeline. The app
needs long-running compute and enough memory for OntoGPT. The operator still needs
to create the VM; this repo alone does not provision cloud resources.

## Data and limitations

Browser storage quotas vary. Export the workspace after important runs. The ZIP
format is capped at 30 MB compressed and 150 MB unpacked for this public worker.
A running job uses temporary server files; wait for completion and a successful
browser sync before closing the tab. The UI syncs during jobs and after refresh.
Neo4j credentials are held in page memory and must be re-entered after reload.
OpenAI usage is charged to the visitor's account.

This is a public compute endpoint with a small per-workspace concurrency limit.
Before promoting it beyond a small study deployment, add operator monitoring and
resource controls for expected traffic.

## Verification

```powershell
.\.venv-testgen\Scripts\python.exe -B pipeline\tests\run_all.py -q
cd pipeline_ui
..\.venv-testgen\Scripts\python.exe -B -m unittest discover -s backend/tests -q
npm run build
```

The generated Gherkin describes policy checks for human review; it is not an
executable test suite.
