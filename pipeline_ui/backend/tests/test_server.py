"""No paid API calls or Neo4j writes. Temporary artifacts stay under pipeline_ui."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from backend import server

sys.path.insert(0, str(server.UI_ROOT / "backend/tools"))
import corpora
import workflow
import yaml


class LocalFiles(unittest.TestCase):
    def setUp(self):
        base = server.UI_ROOT / "data/test-workspaces"
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


class CorpusTests(LocalFiles):
    def write_config(self, filename, name, prefix, file_prefix="test"):
        source = self.root / f"{name}.txt"
        source.write_text("26WE(1) A provision.", encoding="utf-8")
        profile = self.root / "profile.yaml"
        profile.write_text("name: example", encoding="utf-8")
        cfg = {"name": name, "file_prefix": file_prefix, "node_prefix": prefix,
               "paths": {"raw": str(source), "profile": str(profile), "spires_dir": str(self.root / 'spires')},
               "provision": {"pattern": r"(\d+[A-Z]+(?:\(\d+\))?)", "key_prefix": "section"}}
        path = self.root / filename
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return path

    def test_both_configured_corpora_are_discovered(self):
        items = {item['name']: item for item in corpora.discover()}
        self.assertEqual(items['app_schedule1']['nodePrefix'], 'SP_')
        self.assertEqual(items['part_iiic']['nodePrefix'], 'PI_')

    def test_collisions_mark_both_configs_invalid(self):
        self.write_config("corpus.yaml", "first", "TX_", "first")
        self.write_config("corpus.second.yaml", "second", "TX_", "second")
        with patch.object(corpora, "CORPORA", self.root), patch.object(corpora, "DATA", self.root):
            items = corpora.discover()
        self.assertEqual(len(items), 2)
        self.assertTrue(all(x['errors'] for x in items))

    def test_invalid_yaml_never_falls_back_to_schedule_one(self):
        path = self.root / "corpus.yaml"
        path.write_text("[not a mapping]", encoding="utf-8")
        with self.assertRaises(ValueError):
            corpora.strict_config(path)

    def test_prepare_copies_only_selected_prefix_and_rejects_stale_cache(self):
        path = self.write_config("corpus.yaml", "first", "TX_", "first")
        output = self.root / "spires/output"
        output.mkdir(parents=True)
        (output / "first_26WE_1.yaml").write_text("extracted_object: {}")
        (output / "other_26WE_1.yaml").write_text("extracted_object: {}")
        with patch.object(corpora, "CORPORA", self.root), patch.object(corpora, "DATA", self.root):
            cid = corpora.identifier(path)
            _, cfg, copied = corpora.prepare(cid)
            local = Path(cfg['paths']['spires_dir']) / "output"
            self.assertTrue((local / "first_26WE_1.yaml").exists())
            self.assertFalse((local / "other_26WE_1.yaml").exists())
            self.assertTrue(copied.is_relative_to(self.root))
            self.assertEqual(corpora.strict_config(path)['paths']['spires_dir'], str(self.root / "spires"))
            (self.root / "first.txt").write_text("Changed source")
            with self.assertRaisesRegex(ValueError, "changed"):
                corpora.prepare(cid)


class ServerTests(LocalFiles):
    def test_atomic_history_write_retries_windows_lock(self):
        target = self.root / 'job.json'
        target.write_text('{"old": true}')
        replace = Path.replace
        attempts = []
        def locked_once(source, destination):
            attempts.append(source)
            if len(attempts) == 1:
                raise PermissionError('Windows sharing lock')
            return replace(source, destination)
        with patch.object(Path, 'replace', locked_once), patch.object(server.time, 'sleep'):
            server._write_json(target, {'new': True})
        self.assertEqual(json.loads(target.read_text()), {'new': True})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_failed_replace_preserves_previous_snapshot(self):
        target = self.root / 'job.json'
        target.write_text('{"old": true}')
        with patch.object(Path, 'replace', side_effect=PermissionError('locked')), patch.object(server.time, 'sleep'):
            with self.assertRaises(PermissionError):
                server._write_json(target, {'new': True})
        self.assertEqual(json.loads(target.read_text()), {'old': True})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_history_failure_does_not_interrupt_worker(self):
        job = {'id': 'test', 'status': 'queued', 'logs': []}
        (self.root / 'test').mkdir()
        process = Mock(stdout=iter(['[verify] first\n', '[verify] last\n']))
        process.wait.return_value = 0
        with patch.object(server, 'JOB_ROOT', self.root), patch.object(server, '_write_json', side_effect=PermissionError('locked')), patch.object(server.subprocess, 'Popen', return_value=process):
            server._worker(job, ['fake-pipeline'])
        process.wait.assert_called_once()
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['logs'][-1], '[verify] last')
        self.assertIn('persistenceWarning', job)
        self.assertNotIn('test', server.PROCESSES)
        with patch.object(server, 'JOB_ROOT', self.root):
            server._save(job)
        self.assertNotIn('persistenceWarning', job)
        self.assertEqual(json.loads((self.root / 'test/job.json').read_text())['status'], 'succeeded')

    def test_missing_graph_explains_loading_without_altering_pipeline_record(self):
        original = {"status": "failed", "error": "GraphUnavailable: Configured node label :PVI_Provision is not present in the graph"}
        (self.root / 'run_record.json').write_text(json.dumps(original))
        payload = server._run_payload(self.root)
        self.assertIn("selected corpus graph is unavailable", payload['error'])
        self.assertEqual(payload['record'], original)

    def test_current_and_future_block_headers(self):
        for count in (3, 4):
            prompt = f"BLOCK 3 of {count} — CONTEXT\nDescription\n====================\nExact context\n\n====================\nOUTPUT FORMAT\n====================\nFeature: output"
            self.assertEqual(server._context_from_prompt(prompt), "Exact context")
        self.assertEqual(server._context_from_prompt("No context"), "")

    def test_evaluation_modes_and_invalid_settings_are_rejected(self):
        for mode in ("kg", "rp1", "none", "full-corpus"):
            with self.assertRaises(ValueError):
                server._validate_request("generate", {"contextMode": mode})
        for value in (0, 1.5, True, "2"):
            with self.assertRaises(ValueError):
                server._validate_request("preview", {"provisions": ["26WE(1)"], "spiresHopDepth": value})
        server._validate_request("generate", {"provisions": ["6.1", "26WE(1)"], "spiresHopDepth": 1})

    def test_preflight_fingerprint_changes_with_source_or_selection(self):
        path = self.root / "source.txt"
        path.write_text("original")
        item = {"path": str(path)}
        before = server._fingerprint(item, {"onlyProvisions": ["6.1"]})
        self.assertNotEqual(before, server._fingerprint(item, {"onlyProvisions": ["6.2"]}))
        path.write_text("changed")
        self.assertNotEqual(before, server._fingerprint(item, {"onlyProvisions": ["6.1"]}))

    def test_restart_preserves_completed_results_and_marks_active_job_interrupted(self):
        jobs = self.root / "jobs"
        with patch.object(server, "JOB_ROOT", jobs), patch.object(server, "JOBS", {}):
            job = {"id": "test", "kind": "generate", "status": "running", "createdAt": 1, "request": {}, "logs": []}
            server._save(job)
            run = jobs / "test/runs/001"
            run.mkdir(parents=True)
            (run / "run_record.json").write_text(json.dumps({"gherkin": "Feature: preserved", "config": {"provision_id": "26WE(1)"}}))
            server._restore()
            restored = server.JOBS['test']
            self.assertEqual(restored['status'], 'interrupted')
            self.assertEqual(restored['result']['runs'][0]['feature'], 'Feature: preserved')

    def test_build_needs_matching_successful_preflight(self):
        item = {"id": "x", "name": "test", "errors": []}
        with patch.object(server, '_corpora', return_value=[item]), patch.object(server, 'JOBS', {}):
            with self.assertRaisesRegex(ValueError, 'preflight'):
                server._start_job('build', {'corpusId': 'x'})


class WorkflowTests(LocalFiles):
    def test_cached_build_uses_official_driver_without_paid_calls_or_loading(self):
        script = '''
import os, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import corpora
corpora.DATA = Path(sys.argv[2])
item = next(x for x in corpora.discover() if x['name'] == 'part_iiic')
_, cfg, config = corpora.prepare(item['id'])
os.environ['CORPUS_CONFIG'] = str(config)
sys.path.insert(0, str(corpora.ROOT))
import build_corpus
build_corpus.gate_auth = lambda model: None
def forbidden(*args, **kwargs):
    raise AssertionError('No paid extraction or database loading is allowed in this test')
build_corpus.stage_extract = forbidden
from spires import build_spires_graph
build_spires_graph.load = forbidden
import workflow
workflow.DATA = corpora.DATA
original_run = workflow.subprocess.run
def free_commands_only(command, *args, **kwargs):
    if any('verify_exceptions.py' in str(arg) for arg in command):
        raise AssertionError('No paid verification is allowed in this test')
    return original_run(command, *args, **kwargs)
workflow.subprocess.run = free_commands_only
result = workflow.build({'verifyExceptions': True, 'loadGraph': False})
assert result['verification']
assert not any(entry['status'] == 'failed' for entry in result['verification'])
assert not result['loaded']
assert (Path(cfg['paths']['spires_dir']) / 'graph/nodes.csv').exists()
print('CACHED_BUILD_OK')
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(server.UI_ROOT / 'backend/tools'), str(self.root)],
                                cwd=server.PIPELINE_ROOT, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'},
                                capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('CACHED_BUILD_OK', result.stdout)

    def test_commands_keep_arbitrary_ids_corpus_and_unique_batch_outputs(self):
        fake = types.SimpleNamespace(label=lambda value: 'PI_' + value)
        with patch.dict(sys.modules, {'corpus_config': fake}), patch.dict(os.environ, {'CORPUS_CONFIG': 'selected.yaml'}):
            first = workflow.generation_command({'spiresInbound': False, 'spiresProvisionText': True}, '26WE(1)', self.root / '001')
            second = workflow.generation_command({}, '26WE(2)', self.root / '002')
        self.assertIn('PI_Provision', first)
        self.assertIn('selected.yaml', first)
        self.assertIn('26WE(1)', first)
        self.assertIn('--no-spires-inbound', first)
        self.assertIn('--spires-provision-text', first)
        self.assertNotEqual(first[first.index('--output-dir') + 1], second[second.index('--output-dir') + 1])

    def test_loader_refuses_cross_prefix_endpoints_before_writes(self):
        registry = Mock()
        registry.check.return_value = []
        session = Mock()
        session.run.return_value = [{'key': 'actor:entity', 'labels': ['OTHER_Actor']}]
        with self.assertRaisesRegex(ValueError, 'without labels'):
            workflow.check_load(session, {'actor:entity': {}}, 'PI_', 'part', registry)
        self.assertNotIn('MERGE', session.run.call_args.args[0])

    def test_loader_refuses_registry_collisions_without_querying_keys(self):
        registry = Mock()
        registry.check.return_value = ['Prefix owned by another corpus']
        session = Mock()
        with self.assertRaisesRegex(ValueError, 'owned'):
            workflow.check_load(session, {}, 'PI_', 'part', registry)
        session.run.assert_not_called()

    def test_official_provision_listing_for_both_corpora(self):
        for filename, count, example in [('corpus.yaml', 62, '6.1'), ('corpus.part_iiic.yaml', 106, '26WE(1)')]:
            command = [sys.executable, '-B', '-m', 'kg_testgen', '--corpus', str(server.PIPELINE_ROOT / 'corpora' / filename), '--list-provisions']
            result = subprocess.run(command, cwd=server.PIPELINE_ROOT, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}, capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(result.stdout.splitlines()), count)
            self.assertIn(example, result.stdout.splitlines())


if __name__ == '__main__':
    unittest.main()
