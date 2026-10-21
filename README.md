# NormGraph Public Workbench

Public, self-hosted edition of the legal-norm graph and Gherkin generator. This is a
separate repository containing the production pipeline, the website, and two bundled
example graphs. The website includes document review; research notebooks,
historical reports, and previous site runtime files are excluded.

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
- The **Evaluate document** tab checks each generated or uploaded Gherkin against
  one supplied policy document. This uses the visitor's OpenAI key but does not
  require a graph or Neo4j credentials. The document and Gherkins are sent to
  OpenAI, and the document, per-scenario verdicts, citations, and usage are saved
  in the visitor's browser workspace.

The full flow remains available: discover built-in corpora, register and validate a
custom statute, induce its structural profile, run a free build preflight, extract
norms, verify exceptions, build a graph, preview context, and generate/download
Gherkin for one or multiple provisions. New extraction requires OntoGPT and the
visitor's OpenAI key. Pre-existing artifacts can be reused by the build.

## Review a policy document

1. Generate Gherkin, then choose **Evaluate against a document** beside that
   output. You can also select a saved generation run or upload/paste a `.feature`
   file in **Evaluate document**.
2. Paste the complete policy or upload UTF-8 `.txt` or `.md` text. Keep the
   original headings and line breaks for evidence citations.
3. Select Luna, Sol, Astra, or the legacy GPT-4o option. Run the free estimate,
   review its token and cost range, set an approximate spending cap, then run
   the paid review. High-end settings require an explicit token warning check.
4. Read each PASS, FAIL, PARTIAL, or UNCERTAIN result with its explanation and
   checked policy quotes. Download `review.md` or `result.json` from the job's
   Files tab. If the job stops, select it under **Continue a saved review** and
   raise the cap if needed; completed verdicts are reused.

The reviewer judges the supplied document's wording against each Gherkin's
`Then` assertion. PARTIAL means some material parts are supported; UNCERTAIN
means the document cannot settle a material condition. Neither a PASS nor a
FAIL establishes legal compliance or the legal soundness of the Gherkin. The
model can make mistakes; a citation marked **Evidence needs review** was not
verified against the uploaded line and requires human inspection. The free
estimate uses an approximate local token count and indicative list prices;
actual provider billing may differ. Model availability depends on the user's
OpenAI account.

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

## Run one image locally

The standalone image contains the website, Caddy, and both Python environments.
No Compose file or source checkout is needed:

```sh
docker run -d --name normgraph -p 127.0.0.1:8080:80 --tmpfs /tmp/normgraph-visitors:size=1g,mode=1777 ghcr.io/tsag0005/normgraph-public:latest
```

Open `http://localhost:8080`. Stop it with `docker stop normgraph`; remove
the stopped container with `docker rm normgraph`. The default listens on HTTP
for local use. Browser workspaces persist in each visitor's browser.

To run that same image on a public server, point a hostname at the server and
allow inbound ports 80 and 443. Set `SITE_ADDRESS` to the hostname and persist
Caddy's certificate data:

```sh
docker run -d --name normgraph --restart unless-stopped -p 80:80 -p 443:443 \
  -e SITE_ADDRESS=your.hostname.example \
  --tmpfs /tmp/normgraph-visitors:size=1g,mode=1777 \
  -v normgraph_caddy_data:/data -v normgraph_caddy_config:/config \
  ghcr.io/tsag0005/normgraph-public:latest
```

Caddy obtains HTTPS after DNS and the ports are ready. Ask visitors to enter
API keys only over HTTPS. The two-container Compose setup below remains
available for operators who prefer separate web and API services.

## Run the published images

Release tags (`v*`) and manual runs of the [image publishing workflow](.github/workflows/publish-images.yml)
build separate API and web images for Linux x86-64 and ARM64. The workflow publishes
multi-platform `latest`, release, and commit tags to GitHub Container Registry:

- `ghcr.io/tsag0005/normgraph-public-api:latest`
- `ghcr.io/tsag0005/normgraph-public-web:latest`

The images contain the application and its bundled example corpora, but no operator
API keys or Neo4j credentials. Anyone can download the [public Compose file](https://gist.github.com/Tsag0005/436f8abd35f412cdddafddd9b7605b65)
without access to this source repository. Save its `compose.yaml` in an empty folder,
then run:

```bash
docker compose pull
docker compose up -d
```

Open `http://localhost` on that computer. To stop it, run
`docker compose down`. This public Compose file pins the `v0.1.0` release;
collaborators with repository access can use [compose.pull.yaml](compose.pull.yaml)
for the current `latest` images. When running on a public server, set
`SITE_ADDRESS` to a hostname pointing to that server and allow ports 80 and 443;
Caddy will provide HTTPS. The browser workspace still holds each visitor's durable
data. OpenAI usage is charged to the visitor's own key.

Both container packages are Public and can be pulled without a GitHub login.
Publishing public images exposes the application code and bundled artifacts inside
them, even while this source repository remains private. No source file outside this repository is copied
into the images.

For a public deployment, follow [the OCI Always Free guide](deploy/OCI.md).
You can use a free `PUBLIC_IP.sslip.io` hostname, so a purchased domain and a
DNS account are optional. Set `SITE_ADDRESS` to that hostname. Caddy obtains
HTTPS automatically when ports 80 and 443 are reachable. Do not ask visitors
to enter keys over HTTP.
No operator OpenAI or Neo4j credentials belong in the container environment.

An OCI Always Free Ampere A1 VM is a practical target for the Python extraction
stack, subject to account eligibility and capacity. Run the published images
on the VM with Docker Compose. Do not assume a static-site host can run the Python pipeline. The app
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
