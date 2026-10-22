'use client';

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import {
  Database,
  FileCode2,
  Network,
  RefreshCw,
  Play,
  Download,
  CircleStop,
  Gauge,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import {
  NativeSelect,
  NativeSelectOption,
} from '@/components/ui/native-select';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Progress } from '@/components/ui/progress';
import { api, API_BASE, setCredentials, type Corpus, type Job, type Run } from '@/lib/api';
import { importWorkspace, syncWorkspace } from '@/lib/workspace';
import { PolicyReviewForm, PolicyReviewResults } from '@/app/policy-review';
import { buttonVariants } from '@/components/ui/button';

const active = (job?: Job) =>
  !!job && ['queued', 'running', 'cancelling'].includes(job.status);
const defaults = {
  contextMode: 'spires',
  llmModel: 'gpt-4o-mini',
  temperature: 0,
  maxOutputTokens: 4000,
  spiresHopDepth: 1,
  spiresInbound: true,
  spiresExceptionsFirst: true,
  spiresProvisionText: false,
  spiresModalityFilter: '',
  allowUnaugmented: false,
};

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-semibold">{label}</p>
      {children}
      <p className="text-sm leading-6 text-muted-foreground">{help}</p>
    </div>
  );
}
function Toggle({
  label,
  help,
  checked,
  onChange,
}: {
  label: string;
  help: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className="flex cursor-pointer items-start gap-3 rounded-lg border border-border p-3"
    >
      <Checkbox
        id={id}
        className="mt-1"
        checked={checked}
        onCheckedChange={(v) => onChange(v === true)}
        aria-label={label}
      />
      <span>
        <span className="block text-sm font-semibold">{label}</span>
        <span className="mt-1 block text-sm leading-6 text-muted-foreground">
          {help}
        </span>
      </span>
    </label>
  );
}
function TextOutput({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-3 overflow-hidden rounded-xl bg-[#0f2028] text-slate-100">
      <div className="flex justify-end border-b border-white/15 p-2">
        <Button
          size="sm"
          variant="ghost"
          className="text-white hover:bg-white/15 hover:text-white"
          onClick={() => {
            void navigator.clipboard
              .writeText(value)
              .then(() => setCopied(true))
              .catch(() => setCopied(false));
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <pre className="max-h-[36rem] overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-sm leading-6">
        {value}
      </pre>
    </div>
  );
}
function RunOutput({ run, onEvaluate }: { run: Run; onEvaluate: (feature: string) => void }) {
  const d = run.details;
  return (
    <section className="space-y-3 border-b border-border pb-6 last:border-0">
      <h3 className="font-semibold">
        {run.provision || d?.target_provision || 'Clause result'}
      </h3>
      {run.error && (
        <Alert variant="destructive">
          <AlertTitle>Provision failed</AlertTitle>
          <AlertDescription>{run.error}</AlertDescription>
        </Alert>
      )}
      {d && (
        <>
          <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            {[
              ['Norms', d.norms_included],
              ['Provisions', d.provisions_included],
              ['Exceptions', d.exceptions_included],
              ['Context tokens ≈', d.token_estimate],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-lg bg-muted p-3">
                <div className="text-muted-foreground">{label}</div>
                <strong className="text-lg">{value ?? '—'}</strong>
              </div>
            ))}
          </div>
          <p className="text-sm leading-6 text-muted-foreground">
            Inbound: {d.inbound_provisions?.join(', ') || 'none'} · Outbound:{' '}
            {d.outbound_provisions?.join(', ') || 'none'}. Reached provisions
            without usable norms may not appear in the rendered context.
          </p>
        </>
      )}
      {(run.warnings || []).map((warning, i) => (
        <Alert key={i}>
          <AlertTitle>Output warning</AlertTitle>
          <AlertDescription>{warning}</AlertDescription>
        </Alert>
      ))}
      {run.usage && run.usage.usage_source === 'api' && (
        <p className="rounded-lg bg-muted p-3 text-sm">
          Actual model tokens: {String(run.usage.prompt_tokens ?? '—')} input +{' '}
          {String(run.usage.completion_tokens ?? '—')} output ={' '}
          {String(run.usage.total_tokens ?? '—')} total.
        </p>
      )}
      {run.feature && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-semibold">Generated Gherkin</h4>
            <Button size="sm" variant="outline" onClick={() => onEvaluate(run.feature || '')}>
              Evaluate against a document
            </Button>
          </div>
          <TextOutput value={run.feature} />
        </>
      )}
      {(run.queryContext || run.text) && (
        <details open={!run.feature}>
          <summary className="cursor-pointer text-sm font-semibold">
            Retrieved graph context
          </summary>
          <TextOutput value={run.queryContext || run.text || ''} />
        </details>
      )}
      {run.usage && (
        <details>
          <summary className="cursor-pointer text-sm font-semibold">
            Model usage and response metadata
          </summary>
          <p className="mt-2 text-sm text-muted-foreground">
            Actual model usage is reported here. The context token estimate
            above is only the retrieval block.
          </p>
          <TextOutput value={JSON.stringify(run.usage, null, 2)} />
        </details>
      )}
      {run.prompt && (
        <details>
          <summary className="cursor-pointer text-sm font-semibold">
            Full prompt sent to the generator
          </summary>
          <TextOutput value={run.prompt} />
        </details>
      )}
    </section>
  );
}
function Results({ job, cancel, onEvaluate }: { job?: Job; cancel: () => void; onEvaluate: (feature: string) => void }) {
  const result = job?.result;
  return (
    <Card className="min-w-0 border-border/80 shadow-sm">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle>Job results</CardTitle>
          {job && (
            <Badge
              variant={job.status === 'failed' ? 'destructive' : 'secondary'}
            >
              {job.status}
            </Badge>
          )}
        </div>
        <CardDescription>
          {job?.label ||
            'Preview extracted graph context, run a preflight, or select a past job.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!job ? (
          <div className="grid min-h-64 place-items-center text-center text-muted-foreground">
            <div>
              <Network className="mx-auto mb-3 h-8 w-8" />
              <p>Your outputs will appear here.</p>
              <p className="mt-2 text-sm">
                History and files are saved locally between sessions.
              </p>
            </div>
          </div>
        ) : (
          <>
            {active(job) && (
              <>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm">
                    Stage: {job.stage || 'starting'}{' '}
                    {job.progress &&
                      `· ${job.progress.done}/${job.progress.total}`}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={cancel}
                    disabled={job.status === 'cancelling'}
                  >
                    <CircleStop className="h-4 w-4" />
                    Stop job
                  </Button>
                </div>
                <Progress
                  value={
                    job.progress?.total
                      ? (job.progress.done / job.progress.total) * 100
                      : null
                  }
                />
                <p className="text-sm text-muted-foreground">
                  Stopping preserves completed files. A model request already
                  sent may still incur a charge.
                </p>
              </>
            )}
            {job.persistenceWarning && (
              <Alert>
                <AlertTitle>Job history could not be saved</AlertTitle>
                <AlertDescription>{job.persistenceWarning}</AlertDescription>
              </Alert>
            )}
            {(job.error || result?.error) && (
              <Alert variant="destructive">
                <AlertTitle>Job needs attention</AlertTitle>
                <AlertDescription>
                  {job.error || result?.error}
                </AlertDescription>
              </Alert>
            )}
            {result?.summary && <p className="leading-7">{result.summary}</p>}
            {result?.warnings?.map((text, i) => (
              <Alert key={i}>
                <AlertTitle>Keep in mind</AlertTitle>
                <AlertDescription>{text}</AlertDescription>
              </Alert>
            ))}
            <Tabs defaultValue="output">
              <TabsList className="h-auto flex-wrap justify-start">
                <TabsTrigger value="output">Output</TabsTrigger>
                <TabsTrigger value="logs">Stages & logs</TabsTrigger>
                <TabsTrigger value="files">Files</TabsTrigger>
                <TabsTrigger value="settings">Reproduce</TabsTrigger>
              </TabsList>
              <TabsContent value="output" className="space-y-5 pt-3">
                {job.kind === 'review' && <PolicyReviewResults job={job} />}
                {(result?.runs || result?.items || []).map((run, i) => (
                  <RunOutput key={i} run={run} onEvaluate={onEvaluate} />
                ))}
                {result?.profile && <TextOutput value={result.profile} />}
                {result?.verification && (
                  <details>
                    <summary className="cursor-pointer text-sm font-semibold">
                      Exception verification · {result.verification.length}{' '}
                      provisions
                    </summary>
                    <ul className="mt-3 space-y-3">
                      {result.verification.map((entry) => (
                        <li
                          key={entry.provision}
                          className="rounded-lg border p-3 text-sm"
                        >
                          <strong>
                            {entry.provision} · {entry.status}
                          </strong>
                          <p className="mt-1 leading-6 text-muted-foreground">
                            {entry.message}
                          </p>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {!(
                  result?.runs?.length ||
                  result?.items?.length ||
                  result?.profile ||
                  result?.estimate
                ) && (
                  <>
                    <p className="text-sm text-muted-foreground">
                      Validation gates, cost estimates, artifact reuse and stage
                      counts are reported in the log.
                    </p>
                    <TextOutput
                      value={job.logs.join('\n') || 'Waiting for the pipeline…'}
                    />
                  </>
                )}
              </TabsContent>
              <TabsContent value="logs">
                <TextOutput value={job.logs.join('\n') || 'No output yet.'} />
                <p className="mt-2 text-sm text-muted-foreground">
                  The latest 500 lines are shown. Download output.log for the
                  full history.
                </p>
              </TabsContent>
              <TabsContent value="files">
                <div className="space-y-2 py-3">
                  {result?.files?.map((name) => (
                    <a
                      key={name}
                      className="flex items-center gap-2 break-all rounded-lg border p-3 text-sm hover:bg-muted"
                      href={`${API_BASE}/api/jobs/${job.id}/files/${name.split('/').map(encodeURIComponent).join('/')}`}
                    >
                      <Download className="h-4 w-4 shrink-0" />
                      {name}
                    </a>
                  ))}
                  {!result?.files?.length && (
                    <p className="text-sm text-muted-foreground">
                      Files are listed when the job finishes.
                    </p>
                  )}
                </div>
              </TabsContent>
              <TabsContent value="settings">
                <p className="mt-3 text-sm leading-6 text-muted-foreground">
                  This command replays the saved request and may incur charges
                  or load Neo4j again. The logs include each underlying pipeline
                  command and the selected corpus configuration. Source text and
                  graph changes can affect repeated results.
                </p>
                <TextOutput
                  value={job.command || 'Command unavailable for imported runs'}
                />
                <details className="mt-3">
                  <summary className="cursor-pointer text-sm font-semibold">
                    Saved request
                  </summary>
                  <TextOutput value={JSON.stringify(job.request, null, 2)} />
                </details>
              </TabsContent>
            </Tabs>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export default function Home() {
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [corpusId, setCorpusId] = useState('');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobId, setJobId] = useState('');
  const [error, setError] = useState('');
  const [exportNotice, setExportNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [openaiKey, setOpenaiKey] = useState('');
  const [graphBackend, setGraphBackend] = useState<'local' | 'neo4j'>('local');
  const [neo4j, setNeo4j] = useState({ uri: '', username: '', password: '', database: 'neo4j' });
  const [view, setView] = useState('generate');
  const [reviewFeatureText, setReviewFeatureText] = useState('');
  const [query, setQuery] = useState('');
  const [selection, setSelection] = useState<string[]>([]);
  const [manual, setManual] = useState(false);
  const [requirement, setRequirement] = useState('');
  const [provisionId, setProvisionId] = useState('');
  const [settings, setSettings] = useState(defaults);
  const [extractionModel, setExtractionModel] = useState('gpt-4o');
  const [verifyExceptions, setVerifyExceptions] = useState(true);
  const [loadGraph, setLoadGraph] = useState(false);
  const [wipe, setWipe] = useState(false);
  const [confirmPrefix, setConfirmPrefix] = useState('');
  const [pilot, setPilot] = useState('');
  const [draft, setDraft] = useState({
    name: '',
    description: '',
    nodePrefix: '',
    text: '',
    profileText: '',
  });
  const [previewSignature, setPreviewSignature] = useState('');
  const corpus = corpora.find((c) => c.id === corpusId);
  const selectedJob = jobs.find((j) => j.id === jobId);
  const generation = {
    ...settings,
    graphBackend,
    corpusId,
    provisions: manual ? [] : selection,
    requirement: manual ? requirement : '',
    provisionId: manual ? provisionId : '',
  };
  const signature = JSON.stringify(generation);
  const onlyProvisions = pilot.split(/[\s,]+/).filter(Boolean);
  const latest = (kind: string) =>
    jobs.find((j) => j.kind === kind && j.request.corpusId === corpusId &&
      (j.request.graphBackend || 'local') === graphBackend);
  const provisionJob = latest('provisions');
  const provisionList = Array.isArray(provisionJob?.result?.provisions)
    ? provisionJob.result.provisions
    : [];
  const graphStatus = latest('graph-status');
  const preflight = jobs.find(
    (j) =>
      j.kind === 'build' &&
      j.status === 'succeeded' &&
      j.request.corpusId === corpusId &&
      (j.request.graphBackend || 'local') === graphBackend &&
      j.request.estimateOnly === true &&
      j.request.extractionModel === extractionModel &&
      JSON.stringify(j.request.onlyProvisions) ===
        JSON.stringify(onlyProvisions),
  );
  const visible = provisionList.filter((p) =>
    p.toLowerCase().includes(query.toLowerCase()),
  );
  const buildActive = jobs.some(
    (j) => ['build', 'register'].includes(j.kind) && active(j),
  );
  const validCorpus = !!corpus && !corpus.errors.length && corpus.sourceExists;
  const canGenerate =
    validCorpus && (manual ? !!requirement.trim() : !!selection.length);
  const refresh = useCallback(async () => {
    try {
      const [cs, js] = await Promise.all([
        api<Corpus[]>('/api/corpora'),
        api<Job[]>('/api/jobs'),
      ]);
      setCorpora(cs);
      setJobs(js);
      setCorpusId((id) => cs.some((c) => c.id === id && !c.errors.length)
        ? id : cs.find((c) => !c.errors.length)?.id || '');
      setError('');
      await syncWorkspace();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  const hasActive = jobs.some(active);
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => {
      void api<Job[]>('/api/jobs')
        .then((next) => {
          setJobs(next);
          if (!next.some(active)) void refresh();
        })
        .catch((e: Error) => setError(e.message));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [hasActive, refresh]);
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => void syncWorkspace().catch(() => {}), 15000);
    return () => window.clearInterval(timer);
  }, [hasActive]);
  async function start(
    kind: string,
    payload: Record<string, unknown>,
    reveal = true,
  ) {
    setBusy(true);
    setError('');
    try {
      setCredentials({ openai: openaiKey, uri: neo4j.uri, username: neo4j.username,
        password: neo4j.password, database: neo4j.database });
      const job = await api<Job>(`/api/jobs/${kind}`, {
        method: 'POST',
        body: JSON.stringify({ ...payload, graphBackend }),
      });
      setJobs((current) => [job, ...current]);
      if (reveal) setJobId(job.id);
      if (kind === 'preview') setPreviewSignature(signature);
      void syncWorkspace().catch((e: Error) => setError(e.message));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCredentials({ openai: '', uri: '', username: '', password: '', database: '' });
      setBusy(false);
    }
  }
  const corpusNames = useMemo(
    () => new Map(corpora.map((c) => [c.id, c.name])),
    [corpora],
  );

  return (
    <main className="min-h-screen px-4 py-7 sm:px-7 lg:px-10">
      <div className="mx-auto max-w-[1500px] space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="mb-2 text-sm font-semibold uppercase tracking-widest text-primary">
              Browser workspace
            </p>
            <h1 className="text-3xl font-semibold tracking-tight">
              From legislation to test scenarios
            </h1>
            <p className="mt-2 max-w-3xl leading-7 text-muted-foreground">
              Choose a corpus, inspect its graph context, and generate Gherkin.
              Build and validate new corpora in the same workspace.
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              Method: knowledge graph extraction uses SPIRES with a LinkML schema.
            </p>
          </div>
          <Button variant="outline" onClick={() => void refresh()}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </header>
        <Card>
          <CardHeader>
            <CardTitle>Workspace and credentials</CardTitle>
            <CardDescription>
              Corpora, graphs, outputs and history are saved in this browser. Export a
              backup before clearing browser data or switching devices. API credentials
              are held only for this open page and are never included in backups.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5 lg:grid-cols-2">
            <Field label="Your OpenAI API key" help="Required for generation, extraction, verification and model preflight. Your own account is charged for model calls.">
              <Input aria-label="OpenAI API key" type="password" autoComplete="off"
                value={openaiKey} onChange={(e) => setOpenaiKey(e.target.value)}
                placeholder="Enter a project API key" />
            </Field>
            <Field label="Graph storage" help="Local uses graph files in your browser workspace. Neo4j connects to your own database for this session.">
              <NativeSelect aria-label="Graph storage" value={graphBackend}
                onChange={(e) => setGraphBackend(e.target.value as 'local' | 'neo4j')}>
                <NativeSelectOption value="local">Local graph files</NativeSelectOption>
                <NativeSelectOption value="neo4j">My Neo4j database</NativeSelectOption>
              </NativeSelect>
            </Field>
            {graphBackend === 'neo4j' && (
              <div className="space-y-3 lg:col-span-2">
                <div className="grid gap-3 sm:grid-cols-2">
                  <Input aria-label="Neo4j URI" placeholder="neo4j+s://..." value={neo4j.uri}
                    onChange={(e) => setNeo4j({ ...neo4j, uri: e.target.value })} />
                  <Input aria-label="Neo4j username" placeholder="Username" value={neo4j.username}
                    onChange={(e) => setNeo4j({ ...neo4j, username: e.target.value })} />
                  <Input aria-label="Neo4j password" type="password" autoComplete="off"
                    placeholder="Password" value={neo4j.password}
                    onChange={(e) => setNeo4j({ ...neo4j, password: e.target.value })} />
                  <Input aria-label="Neo4j database" placeholder="Database" value={neo4j.database}
                    onChange={(e) => setNeo4j({ ...neo4j, database: e.target.value })} />
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-3 lg:col-span-2">
              <a className={buttonVariants({ variant: 'outline' })}
                href={`${API_BASE}/api/workspace/snapshot`}
                download="normgraph-workspace.zip"
                onClick={() => setExportNotice('Download requested. Check this browser’s Downloads for normgraph-workspace.zip.')}>
                <Download className="h-4 w-4" /> Export workspace
              </a>
              <label className="cursor-pointer rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted">
                Import workspace
                <input type="file" accept=".zip,application/zip" className="sr-only"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void importWorkspace(file).then(refresh).catch((error: Error) => setError(error.message));
                    e.currentTarget.value = '';
                  }} />
              </label>
              {exportNotice && <p role="status" className="w-full text-sm text-muted-foreground">{exportNotice}</p>}
              <p className="w-full text-sm text-muted-foreground">
                Wait for running jobs to finish before exporting. Workspace ZIPs download to the
                device running this browser, not the Docker container.
              </p>
            </div>
          </CardContent>
        </Card>
        {error && (
          <Alert variant="destructive">
            <AlertTitle>Unable to complete that action</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <Card>
          <CardContent className="grid gap-5 pt-6 lg:grid-cols-[minmax(220px,1fr)_2fr]">
            <Field
              label="Selected corpus"
              help="This selection applies to builds, provision lists, previews and generation. Each corpus uses its own graph prefix."
            >
              <NativeSelect
                aria-label="Selected corpus"
                className="w-full"
                value={corpusId}
                onChange={(e) => {
                  setCorpusId(e.target.value);
                  setSelection([]);
                  setQuery('');
                  setPilot('');
                  setConfirmPrefix('');
                  setPreviewSignature('');
                }}
              >
                <NativeSelectOption value="" disabled>
                  Choose a corpus
                </NativeSelectOption>
                {corpora.map((c) => (
                  <NativeSelectOption key={c.id} value={c.id}>
                    {c.name}
                    {c.errors.length ? ' · needs attention' : ''}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </Field>
            {corpus && (
              <div className="space-y-3">
                <p className="leading-7">{corpus.description}</p>
                {corpus.errors.length ? (
                  <p className="text-sm text-destructive">
                    {corpus.errors.join('; ')}
                  </p>
                ) : (
                  <>
                    <div className="flex flex-wrap gap-2">
                      <Badge variant="secondary">
                        Files: {corpus.filePrefix}_*
                      </Badge>
                      <Badge variant="secondary">
                        Graph: {corpus.nodePrefix}*
                      </Badge>
                      <Badge variant="outline">{corpus.buildStatus}</Badge>
                      <Badge variant="outline">
                        {corpus.profileExists
                          ? 'Profile available'
                          : 'Profile missing'}
                      </Badge>
                    </div>
                    <p className="break-all text-sm text-muted-foreground">
                      Structural profile: {corpus.profile}
                    </p>
                    <div className="flex flex-wrap items-center gap-3">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy || active(graphStatus)}
                        onClick={() =>
                          void start('graph-status', { corpusId })
                        }
                      >
                        Check {graphBackend === 'local' ? 'local graph' : 'Neo4j'}
                      </Button>
                      <span className="text-sm">
                        {active(graphStatus)
                          ? 'Checking connection…'
                          : graphStatus?.result?.summary ||
                            graphStatus?.error ||
                            'Graph status has not been checked.'}
                      </span>
                    </div>
                    {graphBackend === 'local' && corpus.buildStatus === 'Local graph ready' && (
                      <p className="text-sm text-muted-foreground">
                        The local graph is ready. This status check is optional; select a provision
                        below to preview or generate a scenario.
                      </p>
                    )}
                    {graphStatus?.result?.warnings?.map((w, i) => (
                      <p className="text-sm text-destructive" key={i}>
                        {w}
                      </p>
                    ))}
                  </>
                )}
              </div>
            )}
          </CardContent>
        </Card>
        <Tabs value={view} onValueChange={setView}>
          <TabsList className="mb-5 grid h-auto w-full grid-cols-2 gap-1 p-1 group-data-horizontal/tabs:h-auto sm:grid-cols-5">
            <TabsTrigger value="generate" className="h-auto min-h-10 w-full flex-none px-3 py-2 text-center whitespace-normal">
              <FileCode2 className="h-4 w-4" />
              Generate scenarios
            </TabsTrigger>
            <TabsTrigger value="review" className="h-auto min-h-10 w-full flex-none px-3 py-2 text-center whitespace-normal">
              <Gauge className="h-4 w-4" />
              Evaluate document
            </TabsTrigger>
            <TabsTrigger value="build" className="h-auto min-h-10 w-full flex-none px-3 py-2 text-center whitespace-normal">
              <Database className="h-4 w-4" />
              Build graph
            </TabsTrigger>
            <TabsTrigger value="corpus" className="h-auto min-h-10 w-full flex-none px-3 py-2 text-center whitespace-normal">Add corpus</TabsTrigger>
            <TabsTrigger value="history" className="col-span-2 h-auto min-h-10 w-full flex-none px-3 py-2 text-center whitespace-normal sm:col-span-1">History</TabsTrigger>
          </TabsList>
          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
            <div className="min-w-0">
              <TabsContent value="generate" className="mt-0">
                <Card>
                  <CardHeader>
                    <CardTitle>Provision and retrieval</CardTitle>
                    <CardDescription>
                      Generates Gherkin assertions for a person reviewing
                      privacy policies. The selected clause and its retrieved
                      graph context inform each scenario.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-5">
                    <Toggle
                      label="Enter a clause manually"
                      help="Use this for pasted text. Otherwise choose exact identifiers from the selected corpus; the pipeline retrieves their source text automatically."
                      checked={manual}
                      onChange={setManual}
                    />
                    {manual ? (
                      <>
                        <Field
                          label="Legislative clause"
                          help="Enter the complete clause you want to test. It should belong to the selected corpus."
                        >
                          <Textarea
                            aria-label="Legislative clause"
                            className="min-h-36"
                            value={requirement}
                            onChange={(e) => setRequirement(e.target.value)}
                          />
                        </Field>
                        <Field
                          label="Provision identifier"
                          help="Optional if the corpus pattern detects it from the text. Examples include 6.1 and 26WE(1)."
                        >
                          <Input
                            aria-label="Provision identifier"
                            value={provisionId}
                            onChange={(e) => setProvisionId(e.target.value)}
                          />
                        </Field>
                      </>
                    ) : (
                      <div className="space-y-3">
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-sm font-semibold">
                            Choose provisions · {selection.length} selected
                          </span>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={
                              !validCorpus || busy || active(provisionJob)
                            }
                            onClick={() =>
                              void start('provisions', { corpusId }, false)
                            }
                          >
                            {active(provisionJob)
                              ? 'Reading…'
                              : 'Load provision list'}
                          </Button>
                        </div>
                        <Input
                          aria-label="Search provisions"
                          placeholder="Search identifiers, e.g. 26WE"
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                        />
                        <div className="max-h-52 overflow-y-auto rounded-lg border p-3">
                          {visible.length ? (
                            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                              {visible.map((pid) => (
                                <label
                                  key={pid}
                                  className="flex cursor-pointer items-center gap-2 text-sm"
                                >
                                  <Checkbox
                                    checked={selection.includes(pid)}
                                    onCheckedChange={(checked) =>
                                      setSelection((s) =>
                                        checked
                                          ? [...s, pid]
                                          : s.filter((p) => p !== pid),
                                      )
                                    }
                                  />
                                  {pid}
                                </label>
                              ))}
                            </div>
                          ) : (
                            <p className="text-sm text-muted-foreground">
                              {provisionJob?.error ||
                                (provisionList.length
                                  ? 'No matching identifiers.'
                                  : 'Load the corpus provision list to begin. This is free.')}
                            </p>
                          )}
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={!selection.length}
                          onClick={() => setSelection([])}
                        >
                          Clear selection
                        </Button>
                        <p className="text-sm leading-6 text-muted-foreground">
                          Each selected provision gets its own generation and
                          output files. A batch makes one generation request per
                          provision, so cost grows with the selection.
                        </p>
                      </div>
                    )}
                    <Field
                      label="Reference hop depth"
                      help="Default 1. Each hop follows another layer of provision references. More hops may add relevant provisions and increase prompt size. The pipeline requires at least one hop."
                    >
                      <Input
                        aria-label="Reference hop depth"
                        type="number"
                        min={1}
                        max={10}
                        value={settings.spiresHopDepth}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            spiresHopDepth: Number(e.target.value),
                          })
                        }
                      />
                    </Field>
                    <Toggle
                      label="Include inbound references"
                      help="Recommended. Finds provisions that refer to or disapply the target. Turning this off can omit exceptions that point back to the target."
                      checked={settings.spiresInbound}
                      onChange={(v) =>
                        setSettings({ ...settings, spiresInbound: v })
                      }
                    />
                    <Toggle
                      label="Present exceptions before conditions"
                      help="Changes the order of information in each retrieved norm, helping surface carve-outs early. It does not remove conditions."
                      checked={settings.spiresExceptionsFirst}
                      onChange={(v) =>
                        setSettings({ ...settings, spiresExceptionsFirst: v })
                      }
                    />
                    <Toggle
                      label="Include verbatim provision text"
                      help="Adds original text for related provisions alongside their extracted norms. Useful for checking meaning, but adds prompt tokens. The target clause is already included separately."
                      checked={settings.spiresProvisionText}
                      onChange={(v) =>
                        setSettings({ ...settings, spiresProvisionText: v })
                      }
                    />
                    <Field
                      label="Related norm modalities"
                      help="All includes every extracted modality. Deontic keeps obligation, prohibition and permission norms, plus applicability norms that may define exceptions. Target norms are retained."
                    >
                      <NativeSelect
                        aria-label="Related norm modalities"
                        className="w-full"
                        value={settings.spiresModalityFilter}
                        onChange={(e) =>
                          setSettings({
                            ...settings,
                            spiresModalityFilter: e.target.value,
                          })
                        }
                      >
                        <NativeSelectOption value="">
                          All extracted norms
                        </NativeSelectOption>
                        <NativeSelectOption value="deontic">
                          Deontic + applicability
                        </NativeSelectOption>
                      </NativeSelect>
                    </Field>
                    <details className="rounded-xl border p-4">
                      <summary className="cursor-pointer font-semibold">
                        Generation settings
                      </summary>
                      <div className="mt-4 space-y-4">
                        <Field
                          label="Generator model"
                          help="Default gpt-4o-mini. Use a model supported by your pipeline and API key; model choice affects cost and output quality."
                        >
                          <Input
                            aria-label="Generator model"
                            value={settings.llmModel}
                            onChange={(e) =>
                              setSettings({
                                ...settings,
                                llmModel: e.target.value,
                              })
                            }
                          />
                        </Field>
                        <Field
                          label="Temperature"
                          help="Default 0. Lower values usually reduce variation; even zero does not guarantee identical results."
                        >
                          <Input
                            aria-label="Temperature"
                            type="number"
                            min={0}
                            max={2}
                            step={0.1}
                            value={settings.temperature}
                            onChange={(e) =>
                              setSettings({
                                ...settings,
                                temperature: Number(e.target.value),
                              })
                            }
                          />
                        </Field>
                        <Field
                          label="Maximum output tokens"
                          help="Default 4000 per provision. Limits response length; too low a value may truncate scenarios. This is not a context limit."
                        >
                          <Input
                            aria-label="Maximum output tokens"
                            type="number"
                            min={1}
                            value={settings.maxOutputTokens}
                            onChange={(e) =>
                              setSettings({
                                ...settings,
                                maxOutputTokens: Number(e.target.value),
                              })
                            }
                          />
                        </Field>
                        <Toggle
                          label="Allow generation after retrieval failure"
                          help="Off by default. If context retrieval fails, allow the generator to use the clause alone. This still costs money, loses graph support and leaves a warning in the result."
                          checked={settings.allowUnaugmented}
                          onChange={(v) =>
                            setSettings({ ...settings, allowUnaugmented: v })
                          }
                        />
                      </div>
                    </details>
                    <div className="rounded-lg bg-muted p-4 text-sm leading-6">
                      Preview retrieves what the graph extracted for the selected
                      clause: norms, linked provisions, and any available conditions
                      or exceptions. It displays this context as text without calling
                      the scenario model. Generation retrieves again, so a graph
                      changed after preview can produce different context. The
                      generated prompt records what was actually sent.
                      {previewSignature && previewSignature !== signature && (
                        <p className="mt-2 font-semibold text-primary">
                          Settings or selection changed. Run a fresh preview.
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-3">
                      <Button
                        variant="outline"
                        disabled={busy || !canGenerate}
                        onClick={() => void start('preview', generation)}
                      >
                        <Network className="h-4 w-4" />
                        Preview extracted graph context · free
                      </Button>
                      <Button
                        disabled={busy || !canGenerate || !openaiKey.trim()}
                        onClick={() => void start('generate', generation)}
                      >
                        <Play className="h-4 w-4" />
                        Generate · paid
                      </Button>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {!validCorpus
                        ? 'Choose a valid corpus first.'
                        : busy
                          ? 'Starting a job; controls will re-enable when its request is accepted.'
                          : !canGenerate
                            ? manual
                              ? 'Enter the clause text to enable preview and generation.'
                              : 'Load the provision list and select at least one provision to enable preview and generation.'
                            : !openaiKey.trim()
                              ? 'Preview is ready. Enter your OpenAI API key to enable paid generation.'
                              : 'Preview and generation are ready. The graph status check is optional.'}
                    </p>
                  </CardContent>
                </Card>
              </TabsContent>
              <TabsContent value="review" keepMounted className="mt-0">
                <PolicyReviewForm jobs={jobs} featureText={reviewFeatureText}
                  setFeatureText={setReviewFeatureText} hasApiKey={!!openaiKey.trim()}
                  busy={busy} start={start} onError={setError} />
              </TabsContent>
              <TabsContent value="build" className="mt-0">
                <Card>
                  <CardHeader>
                    <CardTitle>Validate, estimate, then build</CardTitle>
                    <CardDescription>
                      The official pipeline owns segmentation, extraction,
                      cleaning, verification and graph construction.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-5">
                    {graphBackend === 'local' && corpus?.builtIn && corpus.buildStatus === 'Local graph ready' && (
                      <Alert>
                        <AlertTitle>Bundled graph already available</AlertTitle>
                        <AlertDescription>
                          For the demo, go to Generate scenarios and select a provision. Building again
                          runs paid extraction and is only needed if you intend to replace this graph.
                        </AlertDescription>
                      </Alert>
                    )}
                    <div className="rounded-xl bg-muted p-4">
                      <p className="font-semibold">
                        Reuse existing corpus artifacts
                      </p>
                      <p className="mt-2 text-sm leading-6">
                        {corpus?.artifacts?.raw ?? 0} raw ·{' '}
                        {corpus?.artifacts?.clean ?? 0} cleaned ·{' '}
                        {corpus?.artifacts?.verified ?? 0} verified files
                        available. Source artifacts are copied into this browser
                        workspace. Completed extraction and verification
                        work is reused automatically.
                      </p>
                    </div>
                    <Field
                      label="Extraction and verification model"
                      help="Default gpt-4o, matching the official build workflow. Changing the model affects new work; it does not replace existing extractions."
                    >
                      <Input
                        aria-label="Extraction model"
                        value={extractionModel}
                        onChange={(e) => setExtractionModel(e.target.value)}
                      />
                    </Field>
                    <Field
                      label="Pilot provision identifiers"
                      help="Optional. Separate IDs with spaces or commas, e.g. 26WE(1), 26WE(2). Limits new extraction; graph construction still includes all available corpus artifacts. Leave blank for the whole corpus."
                    >
                      <Input
                        aria-label="Pilot provision identifiers"
                        value={pilot}
                        onChange={(e) => setPilot(e.target.value)}
                      />
                    </Field>
                    <Toggle
                      label="Verify exceptions"
                      help="Runs paid exception recovery where structured norms can be paired with raw sentences. Empty or unpairable items keep cleaned output with a review warning; other provisions continue. API failures block loading after the checks finish. Successful verified outputs are reused. The extraction estimate excludes this pass."
                      checked={verifyExceptions}
                      onChange={setVerifyExceptions}
                    />
                    {graphBackend === 'neo4j' && <Toggle
                      label="Load into Neo4j after building"
                      help="Needed when you select Neo4j for preview or generation. Off by default so a build can stop at downloadable CSVs. Loading changes the selected corpus graph and runs prefix and endpoint checks first."
                      checked={loadGraph}
                      onChange={(v) => {
                        setLoadGraph(v);
                        if (!v) {
                          setWipe(false);
                          setConfirmPrefix('');
                        }
                      }}
                    />}
                    {graphBackend === 'neo4j' && loadGraph && (
                      <>
                        <Toggle
                          label="Replace the selected corpus graph"
                          help={`Delete nodes under ${corpus?.nodePrefix || 'the selected prefix'} before loading. Without replacement, the pipeline merges nodes and relationships; obsolete data may remain.`}
                          checked={wipe}
                          onChange={setWipe}
                        />
                        {wipe && (
                          <Field
                            label="Confirm the prefix to replace"
                            help={`Type ${corpus?.nodePrefix || 'the selected prefix'} exactly. Only that prefix is eligible for deletion.`}
                          >
                            <Input
                              aria-label="Confirm replacement prefix"
                              value={confirmPrefix}
                              onChange={(e) => setConfirmPrefix(e.target.value)}
                            />
                          </Field>
                        )}
                      </>
                    )}
                    {graphBackend === 'neo4j' && <Alert>
                      <AlertTitle>Shared database loading safeguard</AlertTitle>
                      <AlertDescription>
                        The current pipeline loader matches relationship
                        endpoints by key without checking labels. This workbench
                        blocks loading when those keys collide with other graph
                        labels. CSV-only builds remain available. Do not load
                        concurrently from another program.
                      </AlertDescription>
                    </Alert>}
                    <p className="text-sm leading-6 text-muted-foreground">
                      The free preflight checks the structural profile, source
                      hygiene, references, prefix ownership and API-key access,
                      then estimates remaining extraction. It may contact OpenAI
                      to list available models, but makes no paid model call.
                      Estimates use the pipeline’s built-in pricing assumptions.
                    </p>
                    <div className="flex flex-wrap gap-3">
                      <Button
                        variant="outline"
                        disabled={!validCorpus || busy || buildActive || !openaiKey.trim()}
                        onClick={() =>
                          void start('build', {
                            corpusId,
                            extractionModel,
                            onlyProvisions,
                            estimateOnly: true,
                          })
                        }
                      >
                        <Gauge className="h-4 w-4" />
                        Run free preflight
                      </Button>
                      <Button
                        disabled={
                          !validCorpus ||
                          !preflight ||
                          busy ||
                          buildActive ||
                          !openaiKey.trim() ||
                          (wipe && confirmPrefix !== corpus?.nodePrefix)
                        }
                        onClick={() =>
                          void start('build', {
                            corpusId,
                            extractionModel,
                            onlyProvisions,
                            verifyExceptions,
                            loadGraph,
                            wipe,
                            confirmPrefix,
                            estimateOnly: false,
                            preflightId: preflight?.id,
                          })
                        }
                      >
                        <Database className="h-4 w-4" />
                        Build · paid stages
                      </Button>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {preflight
                        ? 'A matching preflight passed. Source changes are checked again before a build starts.'
                        : 'Pass the preflight for this corpus, model and pilot selection to enable building.'}
                    </p>
                  </CardContent>
                </Card>
              </TabsContent>
              <TabsContent value="corpus" className="mt-0">
                <Card>
                  <CardHeader>
                    <CardTitle>Add a corpus</CardTitle>
                    <CardDescription>
                      Source text, profile and configuration are stored locally
                      inside this website.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-5">
                    <Field
                      label="Corpus name"
                      help="A unique name used in the corpus selector and prefix registry."
                    >
                      <Input
                        aria-label="Corpus name"
                        value={draft.name}
                        onChange={(e) =>
                          setDraft({ ...draft, name: e.target.value })
                        }
                      />
                    </Field>
                    <Field
                      label="Description"
                      help="Describe the instrument, part and version so others know what the graph represents."
                    >
                      <Input
                        aria-label="Corpus description"
                        value={draft.description}
                        onChange={(e) =>
                          setDraft({ ...draft, description: e.target.value })
                        }
                      />
                    </Field>
                    <Field
                      label="Neo4j node prefix"
                      help="Choose a unique prefix: 1–8 uppercase letters or numbers, beginning with a letter and ending in an underscore. For example, TX_. Existing prefixes are shown in the corpus selector."
                    >
                      <Input
                        aria-label="New node prefix"
                        value={draft.nodePrefix}
                        onChange={(e) =>
                          setDraft({ ...draft, nodePrefix: e.target.value })
                        }
                      />
                    </Field>
                    <Field
                      label="Statute source text"
                      help="Keep headings, numbering and line structure from the source. Remove page numbers, repeated page headers and broken hyphenation; the pipeline checks source hygiene."
                    >
                      <input
                        aria-label="Upload statute text"
                        type="file"
                        accept=".txt,text/plain"
                        className="mb-2 block w-full text-sm"
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          if (f)
                            void f
                              .text()
                              .then((text) =>
                                setDraft((d) => ({ ...d, text })),
                              );
                        }}
                      />
                      <Textarea
                        aria-label="Statute source text"
                        className="min-h-40 font-mono text-sm"
                        value={draft.text}
                        onChange={(e) =>
                          setDraft({ ...draft, text: e.target.value })
                        }
                      />
                    </Field>
                    <Field
                      label="Structural profile YAML (optional)"
                      help="Paste an existing profile for free validation. Otherwise the pipeline can induce one using a paid model call and validate it against the full text. Review the resulting profile before extraction."
                    >
                      <Textarea
                        aria-label="Structural profile YAML"
                        className="min-h-28 font-mono text-sm"
                        value={draft.profileText}
                        onChange={(e) =>
                          setDraft({ ...draft, profileText: e.target.value })
                        }
                      />
                    </Field>
                    <p className="text-sm leading-6 text-muted-foreground">
                      Profile induction uses {extractionModel}; change it in
                      Build graph. File prefixes are assigned automatically to
                      isolate artifacts. Creating a profile does not extract
                      norms or load Neo4j.
                    </p>
                    <div className="flex flex-wrap gap-3">
                      {!draft.profileText.trim() && (
                        <Button
                          variant="outline"
                          disabled={
                            busy ||
                            buildActive ||
                            !draft.text.trim() ||
                            !draft.name.trim()
                          }
                          onClick={() =>
                            void start('register', {
                              ...draft,
                              extractionModel,
                              estimateOnly: true,
                            })
                          }
                        >
                          Estimate profile induction · free
                        </Button>
                      )}
                      <Button
                        disabled={
                          busy ||
                          buildActive ||
                          !draft.text.trim() ||
                          !draft.name.trim() ||
                          (!draft.profileText.trim() && !openaiKey.trim()) ||
                          !/^[A-Z][A-Z0-9]{0,7}_$/.test(draft.nodePrefix)
                        }
                        onClick={() =>
                          void start('register', {
                            ...draft,
                            extractionModel,
                            estimateOnly: false,
                          })
                        }
                      >
                        {draft.profileText.trim()
                          ? 'Validate profile & save · free'
                          : 'Induce profile & save · paid'}
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </TabsContent>
              <TabsContent value="history" className="mt-0">
                <Card>
                  <CardHeader>
                    <CardTitle>Persistent job history</CardTitle>
                    <CardDescription>
                      Select a job to inspect its settings, stage log and saved
                      files. Interrupted jobs retain completed artifacts.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {jobs.length ? (
                      jobs.map((job) => (
                        <button
                          key={job.id}
                          onClick={() => setJobId(job.id)}
                          className={`flex w-full items-center justify-between gap-3 rounded-xl border p-4 text-left hover:bg-muted ${job.id === jobId ? 'border-primary bg-muted' : ''}`}
                        >
                          <span className="min-w-0">
                            <strong className="block truncate text-sm">
                              {job.kind} ·{' '}
                              {corpusNames.get(String(job.request.corpusId)) ||
                                (job.request.documentName as string) ||
                                (job.request.name as string) ||
                                'Earlier run'}
                            </strong>
                            <span className="text-sm text-muted-foreground">
                              {new Date(job.createdAt * 1000).toLocaleString()}
                            </span>
                          </span>
                          <Badge variant="outline">{job.status}</Badge>
                        </button>
                      ))
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        No saved jobs yet.
                      </p>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>
            </div>
            <Results
              key={jobId}
              job={selectedJob}
              onEvaluate={(feature) => {
                setReviewFeatureText(feature);
                setView('review');
              }}
              cancel={() => {
                if (jobId)
                  void api<Job>(`/api/jobs/${jobId}/cancel`, {
                    method: 'POST',
                    body: '{}',
                  })
                    .then(() => refresh())
                    .catch((e: Error) => setError(e.message));
              }}
            />
          </div>
        </Tabs>
      </div>
    </main>
  );
}
