'use client';

import { useMemo, useRef, useState } from 'react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select';
import { Textarea } from '@/components/ui/textarea';
import type { Job } from '@/lib/api';

type Props = {
  jobs: Job[];
  featureText: string;
  setFeatureText: (value: string) => void;
  hasApiKey: boolean;
  busy: boolean;
  start: (kind: string, payload: Record<string, unknown>) => Promise<void>;
  onError: (value: string) => void;
};

const models = [
  { id: 'gpt-6-luna', label: 'GPT-6 Luna · lower cost' },
  { id: 'gpt-6.1-sol', label: 'GPT-6.1 Sol · balanced' },
  { id: 'gpt-6-astra', label: 'GPT-6 Astra · high end' },
  { id: 'gpt-4o', label: 'GPT-4o · legacy comparison' },
];

const policyPresets = [
  {
    id: 'github-2017',
    label: 'GitHub Privacy Statement · 11 Oct 2017',
    path: '/policies/github_privacy_2017.md',
    source: 'https://github.com/github/site-policy/blob/2017-10-11/Policies/github-privacy-statement.md',
  },
  {
    id: 'github-2018',
    label: 'GitHub Privacy Statement · 25 May 2018',
    path: '/policies/github_privacy_2018.md',
    source: 'https://github.com/github/site-policy/blob/2018-05-25/Policies/github-privacy-statement.md',
  },
];

export function PolicyReviewForm({ jobs, featureText, setFeatureText, hasApiKey, busy, start, onError }: Props) {
  const [documentText, setDocumentText] = useState('');
  const [documentName, setDocumentName] = useState('');
  const [selectedPolicyId, setSelectedPolicyId] = useState('');
  const [loadingPolicy, setLoadingPolicy] = useState(false);
  const policyRequest = useRef(0);
  const [reviewModel, setReviewModel] = useState('gpt-6.1-sol');
  const [reasoningEffort, setReasoningEffort] = useState('medium');
  const [maxCostUsd, setMaxCostUsd] = useState(5);
  const [acknowledged, setAcknowledged] = useState(false);
  const [resumeJobId, setResumeJobId] = useState('');
  const sources = useMemo(() => jobs.flatMap((job) =>
    (job.result?.runs || []).filter((run) => !!run.feature).map((run, index) => ({
      id: `${job.id}:${index}`,
      label: `${job.label} · ${run.provision || `scenario set ${index + 1}`}`,
      feature: run.feature || '',
    }))), [jobs]);
  const selectedSource = sources.find((item) => item.feature === featureText)?.id || '';
  const payload = { featureText, documentText, documentName, reviewModel, reasoningEffort };
  const resumable = jobs.filter((job) => job.kind === 'review' &&
    !['queued', 'running', 'cancelling'].includes(job.status) &&
    !!job.result?.signature && !job.result?.complete);
  const resumeMatch = resumable.find((job) => job.id === resumeJobId &&
    job.request.featureText === featureText && job.request.documentText === documentText &&
    job.request.documentName === documentName && job.request.reviewModel === reviewModel &&
    job.request.reasoningEffort === reasoningEffort);
  const preview = jobs.find((job) => job.kind === 'review-preview' && job.status === 'succeeded' &&
    job.request.featureText === featureText && job.request.documentText === documentText &&
    job.request.documentName === documentName && job.request.reviewModel === reviewModel &&
    job.request.reasoningEffort === reasoningEffort);
  const estimate = preview?.result?.estimate;
  const selectedPolicy = policyPresets.find((item) => item.id === selectedPolicyId);
  const highEnd = reviewModel === 'gpt-6-astra' || reasoningEffort === 'xhigh';
  const ready = !loadingPolicy && !!featureText.trim() && !!documentText.trim() && !!documentName.trim();

  async function choosePolicy(id: string) {
    const request = ++policyRequest.current;
    setSelectedPolicyId(id);
    setDocumentText('');
    const preset = policyPresets.find((item) => item.id === id);
    setDocumentName(preset?.label || '');
    if (!preset) {
      setLoadingPolicy(false);
      return;
    }
    setLoadingPolicy(true);
    try {
      const response = await fetch(preset.path);
      if (!response.ok) throw new Error(`Could not load ${preset.label}`);
      const text = await response.text();
      if (request === policyRequest.current) setDocumentText(text);
    } catch (error) {
      if (request === policyRequest.current) {
        setSelectedPolicyId('');
        setDocumentName('');
        onError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (request === policyRequest.current) setLoadingPolicy(false);
    }
  }

  async function readFile(file: File, receive: (value: string) => void) {
    try {
      if (file.size > 2_000_000) throw new Error('Text uploads must be under 2 MB');
      receive(await file.text());
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Evaluate Gherkins against a document</CardTitle>
        <CardDescription>
          Check each Then assertion against the words in a privacy policy or other supplied
          document. This reviews document content; it does not establish legal compliance.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {!!resumable.length && <div className="space-y-2">
          <label htmlFor="review-resume" className="text-sm font-semibold">Continue a saved review</label>
          <NativeSelect id="review-resume" className="w-full" value={resumeMatch?.id || ''}
            onChange={(event) => {
              const job = resumable.find((item) => item.id === event.target.value);
              setResumeJobId(job?.id || '');
              if (!job) return;
              setFeatureText(String(job.request.featureText || ''));
              ++policyRequest.current;
              setLoadingPolicy(false);
              setSelectedPolicyId('');
              setDocumentText(String(job.request.documentText || ''));
              setDocumentName(String(job.request.documentName || ''));
              setReviewModel(String(job.request.reviewModel || 'gpt-6.1-sol'));
              setReasoningEffort(String(job.request.reasoningEffort || 'medium'));
              setMaxCostUsd(Number(job.request.maxCostUsd || 5));
              setAcknowledged(false);
            }}>
            <NativeSelectOption value="">New review</NativeSelectOption>
            {resumable.map((job) => <NativeSelectOption key={job.id} value={job.id}>
              {job.label} · {job.result?.verdicts?.length || 0}/{job.result?.estimate?.scenarios || '?'} complete
            </NativeSelectOption>)}
          </NativeSelect>
        </div>}
        <div className="space-y-2">
          <label htmlFor="review-saved" className="text-sm font-semibold">Generated Gherkins</label>
          <NativeSelect id="review-saved" className="w-full" value={selectedSource}
            onChange={(event) => setFeatureText(sources.find((item) => item.id === event.target.value)?.feature || '')}>
            <NativeSelectOption value="">Paste or upload a feature file</NativeSelectOption>
            {sources.map((item) => <NativeSelectOption key={item.id} value={item.id}>{item.label}</NativeSelectOption>)}
          </NativeSelect>
          <p className="text-sm text-muted-foreground">Use a saved generation run, upload a .feature file, or paste Gherkin below.</p>
          <input aria-label="Upload Gherkin feature" type="file" accept=".feature,.txt,text/plain"
            className="block w-full text-sm" onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void readFile(file, setFeatureText);
              event.currentTarget.value = '';
            }} />
          <Textarea aria-label="Gherkin scenarios" className="min-h-40 font-mono text-sm"
            value={featureText} onChange={(event) => setFeatureText(event.target.value)}
            placeholder="Feature: ...&#10;Scenario: ...&#10;  Given ...&#10;  When ...&#10;  Then ..." />
        </div>
        <div className="space-y-2">
          <label htmlFor="review-policy-preset" className="text-sm font-semibold">Example policy (optional)</label>
          <NativeSelect id="review-policy-preset" className="w-full" value={selectedPolicyId}
            onChange={(event) => void choosePolicy(event.target.value)}>
            <NativeSelectOption value="">Upload or paste my own document</NativeSelectOption>
            {policyPresets.map((item) => <NativeSelectOption key={item.id} value={item.id}>
              {item.label}
            </NativeSelectOption>)}
          </NativeSelect>
          <p className="text-sm text-muted-foreground">
            These are historical examples from GitHub&apos;s site-policy repository (CC0-1.0),
            not its current policy. {selectedPolicy && <a className="underline" href={selectedPolicy.source}
              target="_blank" rel="noreferrer">View this version at GitHub.</a>}
          </p>
          {loadingPolicy && <p className="text-sm text-muted-foreground">Loading example policy…</p>}
          <label htmlFor="review-document-name" className="text-sm font-semibold">Document name</label>
          <Input id="review-document-name" value={documentName}
            onChange={(event) => { setSelectedPolicyId(''); setDocumentName(event.target.value); }}
            placeholder="GitHub Privacy Statement, 2017" />
          <input aria-label="Upload policy document" type="file" accept=".txt,.md,text/plain,text/markdown"
            className="block w-full text-sm" onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                ++policyRequest.current;
                setLoadingPolicy(false);
                setSelectedPolicyId('');
                setDocumentName(file.name);
                void readFile(file, setDocumentText);
              }
              event.currentTarget.value = '';
            }} />
          <Textarea aria-label="Policy document text" className="min-h-44 font-mono text-sm"
            value={documentText} onChange={(event) => {
              ++policyRequest.current;
              setLoadingPolicy(false);
              setSelectedPolicyId('');
              setDocumentText(event.target.value);
            }}
            placeholder="Paste the complete policy here. Keep headings and line breaks for citations." />
          <p className="text-sm text-muted-foreground">The document and scenarios are sent to OpenAI for review. Their text and the results are saved in your browser workspace backup.</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <label htmlFor="review-model" className="text-sm font-semibold">Evaluation model</label>
            <NativeSelect id="review-model" className="w-full" value={reviewModel}
              onChange={(event) => { setReviewModel(event.target.value); setAcknowledged(false); }}>
              {models.map((model) => <NativeSelectOption key={model.id} value={model.id}>{model.label}</NativeSelectOption>)}
            </NativeSelect>
          </div>
          <div className="space-y-2">
            <label htmlFor="review-effort" className="text-sm font-semibold">Reasoning effort</label>
            <NativeSelect id="review-effort" className="w-full" value={reasoningEffort}
              disabled={reviewModel === 'gpt-4o'}
              onChange={(event) => { setReasoningEffort(event.target.value); setAcknowledged(false); }}>
              {['low', 'medium', 'high', 'xhigh'].map((effort) =>
                <NativeSelectOption key={effort} value={effort}>{effort}</NativeSelectOption>)}
            </NativeSelect>
          </div>
        </div>
        <Button variant="outline" disabled={busy || !ready}
          onClick={() => void start('review-preview', payload)}>
          Estimate review · no model call
        </Button>
        {estimate && (
          <div className="rounded-xl bg-muted p-4 text-sm leading-6">
            <strong>{estimate.scenarios} scenario(s) · {estimate.model}</strong>
            <p>Approximately {estimate.inputTokensApprox.toLocaleString()} input tokens across all calls.
              Input estimate ${estimate.estimatedInputUsd.toFixed(2)}; with the {estimate.outputTokensAllowance.toLocaleString()} token output allowance,
              approximately up to ${estimate.estimatedMaximumUsd.toFixed(2)} at the listed rates.</p>
            <p className="text-muted-foreground">{estimate.pricingBasis}</p>
          </div>
        )}
        <Alert>
          <AlertTitle>{highEnd ? 'High-end evaluation can use many tokens' : 'Evaluation uses your API credits'}</AlertTitle>
          <AlertDescription>
            The complete document is sent once for every Gherkin. Large policies and many
            scenarios can consume hundreds of thousands or millions of tokens. Your OpenAI
            account pays for these calls. The estimate is approximate, and your account&apos;s
            actual pricing may differ.
          </AlertDescription>
        </Alert>
        <div className="space-y-2">
          <label htmlFor="review-cap" className="text-sm font-semibold">Approximate spending cap (USD)</label>
          <Input id="review-cap" type="number" min="0.01" max="1000" step="0.01"
            value={maxCostUsd} onChange={(event) => setMaxCostUsd(Number(event.target.value))} />
          <p className="text-sm text-muted-foreground">The worker checks its estimated list-price usage before each call. Provider billing can differ.</p>
        </div>
        {highEnd && (
          <label className="flex items-start gap-3 rounded-lg border p-3 text-sm">
            <Checkbox checked={acknowledged} onCheckedChange={(value) => setAcknowledged(value === true)}
              aria-label="Acknowledge high token usage" />
            <span>I understand this high-end review may use many tokens and charge my API key.</span>
          </label>
        )}
        <Button disabled={busy || !ready || !preview || !hasApiKey || maxCostUsd <= 0 || maxCostUsd > 1000 || (highEnd && !acknowledged)}
          onClick={() => void start('review', { ...payload, preflightId: preview?.id, maxCostUsd,
            ...(resumeMatch ? { resumeJobId: resumeMatch.id } : {}) })}>
          {resumeMatch ? 'Continue document review · paid' : 'Run document review · paid'}
        </Button>
        {!preview && <p className="text-sm text-muted-foreground">Run a matching free estimate to enable review.</p>}
        {!hasApiKey && <p className="text-sm text-muted-foreground">Enter your OpenAI API key in Workspace and credentials.</p>}
      </CardContent>
    </Card>
  );
}

export function PolicyReviewResults({ job }: { job: Job }) {
  const result = job.result;
  if (!result?.estimate) return null;
  const usage = result.actualUsage;
  return (
    <div className="space-y-4">
      {usage && <p className="rounded-lg bg-muted p-3 text-sm">
        Actual usage: {usage.inputTokens.toLocaleString()} input + {usage.outputTokens.toLocaleString()} output tokens.
        Estimated list-price cost: ${usage.estimatedUsd.toFixed(2)}.
      </p>}
      {result.verdicts?.map((row) => (
        <article key={row.index} className="space-y-3 rounded-xl border p-4">
          <div className="flex flex-wrap items-center gap-2">
            <strong>{row.index}. {row.name}</strong>
            <Badge variant={row.verdict === 'FAIL' ? 'destructive' : 'secondary'}
              className={row.verdict === 'YELLOW' ? 'bg-amber-100 text-amber-950 dark:bg-amber-900 dark:text-amber-100' : ''}>
              {row.verdict === 'YELLOW' ? row.yellowReason : row.verdict}
            </Badge>
            {!!row.evidenceProblems.length && <Badge variant="destructive">Evidence needs review</Badge>}
          </div>
          <p className="leading-6">{row.explanation}</p>
          {row.evidence.length > 0 && <ul className="space-y-2 text-sm">
            {row.evidence.map((item, index) =>
              <li key={index} className="rounded-lg bg-muted p-3">
                Line {item.line}{item.section ? ` · ${item.section}` : ''}: “{item.quote}”
              </li>)}
          </ul>}
          {row.evidenceProblems.map((problem, index) =>
            <p key={index} className="text-sm text-destructive">{problem}</p>)}
          <details>
            <summary className="cursor-pointer text-sm font-semibold">Full Gherkin</summary>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-xs">{row.gherkin}</pre>
          </details>
        </article>
      ))}
      {result.verdicts && !result.complete &&
        <p className="text-sm text-muted-foreground">Completed verdicts are saved as the review runs. An interrupted review may have fewer verdicts than scenarios.</p>}
    </div>
  );
}
