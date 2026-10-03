import concurrent.futures
import copy
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from operations.memory import task_workspace as w
from adapters.openai import task_workspace_hook as hook


def call_process(request):
    return w.execute(request)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'main'
        self.root.mkdir()
        self.git('init', '-q')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture')
        self.other = Path(self.temp.name).resolve() / 'other'
        self.git('worktree', 'add', '-q', '-b', 'other', str(self.other))
        self.serial = 0
        self.base = {'schema_version': 1, 'worktree': str(self.root), 'session_id': 'coordinator', 'host': 'codex',
                     'issue_id': 'TEST-1', 'issue_uuid': 'fixture-issue-uuid'}
        self.base['coordinator'] = w.participant_key(self.base)
        reg = self.call('register', main_worktree=str(self.root))
        self.base['repo_id'] = reg['repo_id']
        self.require_ok(w.execute(self.req('register', worktree=str(self.other), main_worktree=str(self.root), repo_id=None)))

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True).stdout

    def req(self, operation, **kwargs):
        self.serial += 1
        request = {**self.base, 'operation': operation, 'request_id': str(uuid.uuid4()), **kwargs}
        return {k: v for k, v in request.items() if v is not None}

    def require_ok(self, result):
        self.assertTrue(result['ok'], result)
        return result

    def call(self, operation, **kwargs):
        return self.require_ok(w.execute(self.req(operation, **kwargs)))

    def create(self):
        result = self.call('create')
        self.base['binding_generation'] = result['binding_generation']
        return result

    def state(self):
        return json.loads((self.root / '.task/.control/issues/TEST-1/state.json').read_text())

    def evidence(self):
        return [{'id': 'fixture', 'locator': 'fixture://test-evidence', 'sha256': 'a' * 64}]

    def ready(self):
        self.call('scope', expected_revision=self.state()['revision'], target_participant=self.base['coordinator'], packet=[])
        self.call('acknowledge', packet_digest=w.sha(w.canonical([])))

    def observations(self, export):
        return [{'id': 'part-' + str(i), 'url': 'https://linear.app/test/document/part-' + str(i),
                 'issue': self.base['issue_uuid'], 'updatedAt': '2026-10-03T00:00:00Z',
                 'origin': 'linear_get_document', 'request_id': 'fixture-get', 'content': part['content']}
                for i, part in enumerate(export['parts'])]

    def seal(self):
        self.call('outcome', expected_revision=self.state()['revision'], disposition='cancelled', evidence=self.evidence())
        return self.call('archive-prepare', expected_revision=self.state()['revision'], seal=True)

    def archive(self):
        export = self.seal()
        observations = self.observations(export)
        index = self.call('archive-index', observations=observations)
        observations.append({'id': 'index', 'url': 'https://linear.app/test/document/index',
                             'issue': self.base['issue_uuid'], 'updatedAt': '2026-10-03T00:00:00Z',
                             'origin': 'linear_get_document', 'request_id': 'fixture-get', 'content': index['content']})
        self.call('archive-verify', observations=observations)
        return observations

    def cleanup_request(self, observations):
        challenge = self.call('cleanup-plan')['cleanup_challenge']
        observations = [{**o, 'request_id': challenge} for o in observations]
        return self.req('cleanup-commit', observations=observations, cleanup_challenge=challenge)


class LifecycleTests(Fixture):
    def test_T01_cross_worktree_concurrent_create_and_isolation(self):
        requests = [self.req('create'), self.req('create', worktree=str(self.other), binding_generation=1)]
        with multiprocessing.get_context('fork').Pool(2) as pool:
            results = pool.map(call_process, requests)
        for r in results:
            self.require_ok(r)
        self.assertTrue((self.other / '.task/TEST-1').is_symlink())
        self.assertEqual((self.other / '.task/TEST-1').resolve(), self.root / '.task/TEST-1')
        second = self.req('create', issue_id='TEST-2', issue_uuid='second', session_id='second')
        second['coordinator'] = w.participant_key(second)
        self.require_ok(w.execute(second))
        self.assertNotEqual((self.root / '.task/TEST-1/roadmap.md').read_bytes(), (self.root / '.task/TEST-2/roadmap.md').read_bytes())

    def test_T02_invalid_ids_links_foreign_registration(self):
        for identifier in ['../TEST-1', 'test-1', 'TEST-01', 'TEST-0', 'TEST-1/', 'ТEST-1', 'TEST-1\n']:
            self.assertFalse(w.execute(self.req('create', issue_id=identifier))['ok'])
        sentinel = Path(self.temp.name).resolve() / 'sentinel'
        sentinel.write_text('outside')
        self.create()
        note = self.root / '.task/TEST-1/context/escape.md'
        note.symlink_to(sentinel)
        self.assertFalse(w.execute(self.req('ready'))['ok'])
        self.assertEqual(sentinel.read_text(), 'outside')
        note.unlink()
        os.link(sentinel, note)
        self.assertFalse(w.execute(self.req('ready'))['ok'])
        self.assertEqual(sentinel.read_text(), 'outside')

    def test_T03_resume_idempotency_adoption_generation(self):
        request = self.req('create')
        first = self.require_ok(w.execute(request))
        self.assertEqual(first, w.execute(request))
        self.base['binding_generation'] = first['binding_generation']
        before = (self.root / '.task/TEST-1/roadmap.md').read_bytes()
        self.call('resume')
        self.assertEqual(before, (self.root / '.task/TEST-1/roadmap.md').read_bytes())
        self.assertEqual(w.execute(self.req('resume', binding_generation=999))['code'], 'STALE_BINDING')
        self.assertEqual(w.execute({**request, 'issue_uuid': 'different'})['code'], 'ISSUE_MISMATCH')
        manual = self.root / '.task/MANUAL-2'
        (manual / 'context').mkdir(parents=True)
        (manual / 'roadmap.md').write_text('# Preserve my exact formatting\n')
        request = self.req('adopt', issue_id='MANUAL-2', session_id='manual', binding_generation=None,
                           issue_uuid='manual', evidence=self.evidence())
        key = w.participant_key(request)
        request.update(coordinator=key, inventory={'roadmap.md': w.sha((manual / 'roadmap.md').read_bytes())}, owners={'roadmap.md': key})
        self.require_ok(w.execute(request))
        self.assertEqual((manual / 'roadmap.md').read_text(), '# Preserve my exact formatting\n')
        self.assertEqual(len((manual / 'events.jsonl').read_text().splitlines()), 1)

    def test_T04_concurrent_update_and_owner(self):
        self.create()
        state = self.state()
        requests = [self.req('update', path='roadmap.md', content='# ' + str(i), expected_revision=state['revision'],
                             old_digest=state['files']['roadmap.md'], provenance={'sources': self.evidence(), 'status': 'draft', 'applicability': 'fixture'}) for i in range(2)]
        with multiprocessing.get_context('fork').Pool(2) as pool:
            results = pool.map(call_process, requests)
        self.assertEqual(sum(r['ok'] for r in results), 1, results)
        self.assertIn('REVISION_CONFLICT', [r['code'] for r in results])

    def test_T05_transaction_boundaries_exactly_once(self):
        for point in ['intent', 'payload:roadmap.md', 'payload:events.jsonl', 'payload-flush', 'state', 'transaction-complete']:
            with self.subTest(point=point):
                request = self.req('create', issue_id='CRASH-' + str(self.serial + 1), session_id=point)
                request['coordinator'] = w.participant_key(request)
                def fail(actual):
                    if actual == point:
                        raise OSError('injected')
                w.FAILPOINT = fail
                try:
                    self.assertFalse(w.execute(request)['ok'])
                finally:
                    w.FAILPOINT = None
                self.require_ok(w.execute(request))
                events = (self.root / '.task' / request['issue_id'] / 'events.jsonl').read_bytes()
                self.assertEqual(w.validate_events(events)[0], 1)

    def test_T06_concurrent_events_secret_rejection_and_corruption(self):
        self.create()
        requests = [self.req('event', event_type='check', event={'code': 'OK'}) for _ in range(12)]
        with multiprocessing.get_context('fork').Pool(4) as pool:
            for r in pool.map(call_process, requests):
                self.require_ok(r)
        path = self.root / '.task/TEST-1/events.jsonl'
        self.assertEqual(w.validate_events(path.read_bytes())[0], 13)
        self.assertFalse(w.execute(self.req('event', event_type='check', event={'summary': 'SECRET-MARKER'}))['ok'])
        self.assertNotIn(b'SECRET-MARKER', path.read_bytes())
        with path.open('ab') as stream:
            stream.write(b'{"interrupted":')
        self.assertEqual(w.execute(self.req('ready'))['code'], 'SCOPE_MISSING')
        self.assertTrue(path.read_bytes().endswith(b'\n'))

    def test_T07_scope_and_stale_source(self):
        self.create()
        source = Path(self.temp.name).resolve() / 'source.md'
        source.write_text('required source')
        packet = [{'id': 'source', 'locator': str(source), 'sha256': w.sha(source.read_bytes()), 'required': True,
                   'authority': 'fixture', 'reason': 'test', 'stage': 'review', 'reader': self.base['coordinator']}]
        self.call('scope', expected_revision=self.state()['revision'], target_participant=self.base['coordinator'], packet=packet)
        read = self.call('read')
        self.assertEqual(len(read['references']), 1)
        self.assertNotIn('roadmap.md', json.dumps(read))
        self.call('acknowledge', packet_digest=read['packet_digest'])
        self.call('ready')
        source.write_text('changed')
        self.assertEqual(w.execute(self.req('ready'))['code'], 'SOURCE_STALE')

    def test_T08_active_in_review_pending_retained(self):
        self.create()
        self.ready()
        self.assertEqual(w.execute(self.req('cleanup-plan'))['code'], 'RETAINED')
        self.call('tool-start', tool_id='tool-1')
        self.assertEqual(w.execute(self.req('detach', evidence=self.evidence()))['code'], 'PENDING_OPERATION')
        self.assertEqual(w.execute(self.req('archive-prepare', expected_revision=self.state()['revision'], seal=True))['code'], 'PENDING_OPERATION')
        self.call('tool-complete', tool_id='tool-1', completed=True)
        self.call('outcome', expected_revision=self.state()['revision'], disposition='in_review', evidence=self.evidence())
        self.assertEqual(w.execute(self.req('cleanup-plan'))['code'], 'RETAINED')

    def test_T09_archive_validation_and_fresh_cleanup(self):
        self.create()
        observations = self.archive()
        for mutate in ['parent', 'part', 'version']:
            bad = copy.deepcopy(observations)
            if mutate == 'parent': bad[0]['issue'] = 'foreign'
            if mutate == 'part': bad = bad[1:]
            if mutate == 'version': bad[0]['updatedAt'] = 'changed'
            self.assertFalse(w.execute(self.req('archive-verify', observations=bad))['ok'])
            self.assertTrue((self.root / '.task/TEST-1').is_dir())
        self.assertFalse(w.execute(self.req('cleanup-commit', observations=observations))['ok'])
        self.require_ok(w.execute(self.cleanup_request(observations)))
        self.assertFalse((self.root / '.task/TEST-1').exists())

    def test_T10_cleanup_interruption_and_outside_sentinel(self):
        self.create()
        observations = self.archive()
        request = self.cleanup_request(observations)
        sentinel = self.root / '.task/OTHER-2'
        sentinel.mkdir()
        (sentinel / 'keep').write_text('keep')
        def fail(point):
            if point == 'quarantine': raise OSError('injected')
        w.FAILPOINT = fail
        try:
            self.assertFalse(w.execute(request)['ok'])
        finally:
            w.FAILPOINT = None
        self.require_ok(w.execute(request))
        self.assertEqual((sentinel / 'keep').read_text(), 'keep')
        self.assertEqual(self.state()['storage'], 'cleaned')

    def test_T11_restore_and_repeated_restore(self):
        self.create()
        observations = self.archive()
        self.require_ok(w.execute(self.cleanup_request(observations)))
        self.assertEqual(w.execute(self.req('resume'))['code'], 'RECOVERY_REQUIRED')
        restore = self.req('restore', observations=observations)
        result = self.require_ok(w.execute(restore))
        self.assertEqual(result, w.execute(restore))
        self.assertEqual(self.state()['disposition'], 'active')
        self.assertGreater(result['binding_generation'], 1)
        self.assertEqual(w.validate_events((self.root / '.task/TEST-1/events.jsonl').read_bytes())[0], 4)

    def test_T14_real_adapter_bootstrap_argument_gate(self):
        event = {'hook_event_name': 'UserPromptSubmit', 'cwd': str(self.root), 'session_id': 'coordinator', 'prompt': 'Task: TEST-1'}
        hook.handle(event)
        request = self.req('create')
        command = shlex.join([hook.PYTHON, str(hook.LIFECYCLE), '--request-json', json.dumps(request)])
        event.update(hook_event_name='PreToolUse', tool_name='Bash', tool_use_id='test', tool_input={'command': command, 'login': False})
        self.assertEqual(hook.handle(event), {})
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        for bad in [command + '; touch sentinel', 'env ' + command, command + ' > output', command + ' && true', command.replace('/usr/bin/python3', 'python3', 1)]:
            event['tool_input']['command'] = bad
            self.assertFalse(hook.bootstrap(event))
        self.assertNotIn('permissionDecision":"allow', json.dumps(hook.handle(event)))

    def test_T16_ignore_patterns_keep_products(self):
        (self.root / '.gitignore').write_bytes((ROOT / '.gitignore').read_bytes())
        ignored = ['.task/TEST-1/roadmap.md', '.DS_Store', 'nested/.DS_Store', 'nested/._data', 'Thumbs.db',
                   'nested/Desktop.ini', 'nested/.Trash-1000/file', '$RECYCLE.BIN/file']
        kept = ['docs/task-workspace.md', '.codex/hooks.json', 'core/templates/task-workspace/roadmap.md',
                'nested/product.ini', 'product.cab', '.env.example', '.github/workflows/check.yml']
        for path in ignored:
            self.assertEqual(subprocess.run(['git', '-C', str(self.root), 'check-ignore', '--no-index', '-q', path]).returncode, 0, path)
        for path in kept:
            self.assertEqual(subprocess.run(['git', '-C', str(self.root), 'check-ignore', '--no-index', '-q', path]).returncode, 1, path)


if __name__ == '__main__':
    unittest.main()
