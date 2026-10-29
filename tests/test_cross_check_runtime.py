import importlib.util
import json
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/cross-check/scripts'
sys.path.insert(0, str(SCRIPTS))
import run_cli
from claude_events import read_claude, quota_fallback_reason


class RuntimeFailureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'prompt').write_text('synthetic')
        self.args = SimpleNamespace(command=['synthetic'], cwd=str(self.root), timeout=0,
                                    stdin=str(self.root/'prompt'), stdout=str(self.root/'out'),
                                    stderr=str(self.root/'err'))

    def child(self, wait_error=None):
        child = SimpleNamespace(pid=424242, returncode=None)
        child.poll = lambda: None
        def kill():
            child.returncode = -signal.SIGKILL
        def wait(timeout):
            if wait_error:
                raise wait_error
            child.returncode = -signal.SIGKILL
            return child.returncode
        child.kill = kill
        child.wait = wait
        return child

    def test_group_denial_preserves_timeout_and_reaps_only_owned_child(self):
        child = self.child()
        with patch.object(run_cli.subprocess, 'Popen', return_value=child), \
             patch.object(run_cli.os, 'killpg', side_effect=PermissionError(1, 'synthetic denial')), \
             patch.object(run_cli, 'TERM_GRACE_SEC', 0):
            result = run_cli.run_once(self.args)
        self.assertEqual((result['exit_code'], result['timeout']), (124, True))
        self.assertTrue(result['process_reaped'])
        self.assertIsNone(result['group_cleanup_complete'])
        self.assertEqual(result['pid'], result['pgid'])
        self.assertIn('killpg', [e['operation'] for e in result['cleanup_errors']])
        self.assertEqual(child.returncode, -signal.SIGKILL)
        json.dumps(result)

    def test_group_already_absent_is_not_cleanup_failure(self):
        with patch.object(run_cli.subprocess, 'Popen', return_value=self.child()), \
             patch.object(run_cli.os, 'killpg', side_effect=ProcessLookupError()), \
             patch.object(run_cli, 'TERM_GRACE_SEC', 0):
            result = run_cli.run_once(self.args)
        self.assertTrue(result['timeout'])
        self.assertTrue(result['group_cleanup_complete'])
        self.assertEqual(result['cleanup_errors'], [])

    def test_failed_wait_is_bounded_and_preserves_unreaped_state(self):
        child = self.child(subprocess.TimeoutExpired('synthetic', 2))
        with patch.object(run_cli.subprocess, 'Popen', return_value=child), \
             patch.object(run_cli.os, 'killpg', side_effect=PermissionError(1, 'synthetic denial')), \
             patch.object(run_cli, 'TERM_GRACE_SEC', 0):
            result = run_cli.run_once(self.args)
        self.assertFalse(result['process_reaped'])
        self.assertTrue(result['timeout'])
        self.assertIn('wait_direct_child', [e['operation'] for e in result['cleanup_errors']])

    def test_input_open_failure_still_returns_serializable_result(self):
        self.args.stdin = str(self.root/'missing')
        result = run_cli.run_once(self.args)
        self.assertEqual(result['exit_code'], 1)
        self.assertIsNone(result['pid'])
        self.assertIn('FileNotFoundError', result['runner_error'])
        json.dumps(result)


class ClaudeEventsTest(unittest.TestCase):
    def fixture(self):
        path = Path(__file__).parent / 'fixtures/claude-session-quota.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_observed_sdk_quota_fixture_and_disabled_extra_usage(self):
        parsed = self.parse(self.fixture())
        self.assertTrue(parsed['valid'])
        self.assertEqual(parsed['error_kind'], 'usage_limit_reached')
        self.assertEqual(parsed['error_status'], 429)
        self.assertEqual(parsed['error_origin'], 'first_party')
        self.assertEqual(quota_fallback_reason(parsed, 'claude-fable-5-1'),
                         'fable_request_usage_limit')
        for changes in ({'overageDisabledReason': 'org_level_disabled_until'},
                        {'overageDisabledReason': 'spend_cap_reached'},
                        {'overageDisabledReason': 'user_consent_required'},
                        {'overageStatus': 'allowed'}, {'isUsingOverage': True},
                        {'limitScope': 'organization'}):
            with self.subTest(changes=changes):
                events = self.fixture()
                events[0]['api_error_params']['rate_limit_info'].update(changes)
                self.assertIsNone(quota_fallback_reason(self.parse(events), 'claude-fable-5-1'))

    def test_sdk_aliases_match_and_conflicts_fail_closed(self):
        pairs = (('api_error', 'api_error_kind'), ('api_error', 'apiError'),
                 ('api_error_params', 'apiErrorParams'),
                 ('is_api_error_message', 'isApiErrorMessage'))
        for primary, alias in pairs:
            events = self.fixture()
            events[0][alias] = events[0][primary]
            self.assertTrue(self.parse(events)['valid'])
            for conflict in ('conflict', {}, False, 1):
                with self.subTest(alias=alias, conflict=conflict):
                    events = self.fixture()
                    events[0][alias] = conflict
                    parsed = self.parse(events)
                    self.assertFalse(parsed['valid'])
                    self.assertIsNone(quota_fallback_reason(parsed, 'claude-fable-5-1'))

    def test_conflicting_failure_fields_across_events_fail_closed(self):
        fields = {'api_error': 'authentication_failed', 'api_error_status': 401,
                  'api_error_origin': 'third_party', 'is_api_error_message': False,
                  'api_error_params': {'rate_limit_info': {'status': 'allowed'}}}
        for field, conflict in fields.items():
            for first in (True, False):
                with self.subTest(field=field, first=first):
                    events = self.fixture()
                    extra = {'type': 'assistant', 'session_id': events[0]['session_id'], field: conflict}
                    events.insert(0 if first else 1, extra)
                    parsed = self.parse(events)
                    self.assertFalse(parsed['valid'])
                    self.assertIsNone(quota_fallback_reason(parsed, 'claude-fable-5-1'))

    def test_sdk_quota_attestation_and_exact_types_are_required(self):
        fields = {'api_error_status': True, 'api_error_origin': [],
                  'is_api_error_message': 1, 'api_error': {}, 'api_error_params': []}
        for field, bad in fields.items():
            with self.subTest(field=field):
                events = self.fixture()
                events[0][field] = bad
                parsed = self.parse(events)
                self.assertFalse(parsed['valid'])
                self.assertIsNone(quota_fallback_reason(parsed, 'claude-fable-5-1'))
        for field in ('api_error_status', 'api_error_origin', 'is_api_error_message'):
            events = self.fixture()
            for event in events:
                event.pop(field, None)
            self.assertIsNone(quota_fallback_reason(self.parse(events), 'claude-fable-5-1'))

    def test_nested_alias_and_event_types_remain_distinct_after_public_capture(self):
        from cross_check_capture import public_event
        cases = (('isUsingOverage', False, 0), ('isUsingOverage', False, 0.0),
                 ('resetsAt', 1793132400, 1793132400.0),
                 ('nestedHistory', [False, True], [0, 1]),
                 ('nestedHistory', [False, True], [True, False]))
        for field, original, conflict in cases:
            for aliases in (True, False):
                for first in (True, False):
                    with self.subTest(field=field, aliases=aliases, first=first):
                        events = self.fixture()
                        info = events[0]['api_error_params']['rate_limit_info']
                        info[field] = original
                        alternative = json.loads(json.dumps(events[0]['api_error_params']))
                        alternative['rate_limit_info'][field] = conflict
                        if aliases:
                            events[0]['apiErrorParams'] = alternative
                        else:
                            events.insert(0 if first else 1, {
                                'type': 'assistant', 'session_id': events[0]['session_id'],
                                'api_error_params': alternative})
                        parsed = self.parse([public_event(e) for e in events])
                        self.assertFalse(parsed['valid'])
                        self.assertTrue(parsed['schema_errors'])
                        self.assertIsNone(quota_fallback_reason(parsed, 'claude-fable-5-1'))

    def test_equal_nested_dicts_allow_different_key_order(self):
        events = self.fixture()
        info = events[0]['api_error_params']['rate_limit_info']
        info['nestedHistory'] = [{'enabled': False, 'value': 0}, True]
        reordered = dict(reversed(list(info.items())))
        events[0]['apiErrorParams'] = {'rate_limit_info': reordered}
        events.insert(1, {'type': 'assistant', 'session_id': events[0]['session_id'],
                          'api_error_params': {'rate_limit_info': reordered}})
        parsed = self.parse(events)
        self.assertTrue(parsed['valid'])
        self.assertIsNone(quota_fallback_reason(parsed, 'claude-fable-5-1'))
        del info['nestedHistory']; del reordered['nestedHistory']
        parsed = self.parse(events)
        self.assertTrue(parsed['valid'])
        self.assertEqual(quota_fallback_reason(parsed, 'claude-fable-5-1'),
                         'fable_request_usage_limit')

    def parse(self, events):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'events'
            path.write_text('\n'.join(json.dumps(e) for e in events))
            return read_claude(str(path))

    def quota(self, **info):
        return self.parse([
            {'type': 'assistant', 'is_api_error_message': True, 'error': 'rate_limit',
             'api_error_kind': 'usage_limit_reached', 'is_api_error_message': True, 'api_error_status': 429, 'api_error_origin': 'first_party', 'api_error_params': {'rate_limit_info': {
                 'status': 'rejected', 'isUsingOverage': False, 'rateLimitType': 'seven_day', **info}},
             'message': {'content': [{'type': 'thinking', 'thinking': 'hidden'}, {'type': 'text', 'text': 'quota failure'}]}},
            {'type': 'result', 'is_error': True, 'result': 'quota failure', 'modelUsage': {}}])

    def test_structured_account_limit_on_fable_is_eligible(self):
        parsed = self.quota()
        self.assertEqual(quota_fallback_reason(parsed, 'claude-fable-5-1'), 'fable_request_usage_limit')
        self.assertNotIn('hidden', json.dumps(parsed))
        self.assertEqual(parsed['models'], [])

    def test_usage_fallback_rejects_unknown_billing_diagnostics(self):
        events = self.fixture()
        for changes in ({'spend_cap': 'reached'}, {'credit_balance': 0},
                        {'consent_required': True}, {'unknown': None}):
            with self.subTest(changes=changes):
                sample = json.loads(json.dumps(events))
                sample[0]['api_error_params'].update(changes)
                self.assertIsNone(quota_fallback_reason(self.parse(sample), 'claude-fable-5-1'))
        for changes in ({'spend_cap': 'reached'}, {'credit_balance': 0}, {'unknown': None}):
            with self.subTest(nested=changes):
                sample = json.loads(json.dumps(events))
                sample[0]['api_error_params']['rate_limit_info'].update(changes)
                self.assertIsNone(quota_fallback_reason(self.parse(sample), 'claude-fable-5-1'))
        sample = json.loads(json.dumps(events))
        sample.insert(1, {'type': 'rate_limit_event', 'session_id': sample[-1]['session_id'],
                          'rate_limit_info': {'spend_cap': 'reached'}})
        self.assertIsNone(quota_fallback_reason(self.parse(sample), 'claude-fable-5-1'))

    def test_credit_caps_unknown_and_nonquota_errors_never_switch(self):
        cases = [dict(rateLimitType='overage'), dict(rateLimitType='seven_day_overage_included'),
                 dict(rateLimitType='unknown'), dict(isUsingOverage=True),
                 dict(overageDisabledReason='org_level_disabled_until'), dict(limitScope='organization')]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertIsNone(quota_fallback_reason(self.quota(**changes), 'claude-fable-5-1'))
        for kind in ('authentication_failed', 'model_requires_usage_credits', 'rate_limit', 'unknown'):
            parsed = self.quota(); parsed['error_kind'] = kind
            self.assertIsNone(quota_fallback_reason(parsed, 'claude-fable-5-1'))
        self.assertIsNone(quota_fallback_reason(self.quota(), 'claude-opus-5-5'))

    def test_malformed_external_shapes_are_unverified_and_preserve_raw(self):
        terminal = {'type':'result','session_id':'s','result':'answer','modelUsage':{'model':{}}}
        malformed = [None, 'diagnostic', [],
                     {'type':'assistant','message':'diagnostic'},
                     {'type':'assistant','message':{'content':{}}},
                     {'type':'assistant','message':{'content':['text']}},
                     {'type':'assistant','message':{'content':[{'type':'text','text':7}]}},
                     dict(terminal, modelUsage=[]), dict(terminal, modelUsage={'model':'bad'}),
                     dict(terminal, result={}), dict(terminal, session_id={}),
                     dict(terminal, is_error='false'), {'type':'system','session_id':[]},
                     {'type':'assistant','api_error_kind':{},'api_error_params':[]}]
        for value in malformed:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'raw'
                original=json.dumps(value) + '\n' + json.dumps(terminal)
                path.write_text(original)
                parsed=read_claude(str(path))
                self.assertFalse(parsed['valid'])
                self.assertTrue(parsed['schema_errors'])
                self.assertIsNone(quota_fallback_reason(parsed,'claude-fable-5-1'))
                self.assertEqual(path.read_text(),original)

    def test_auth_synthetic_message_is_not_observed_model(self):
        parsed = self.parse([{'type':'assistant', 'error':'authentication_failed',
                             'message': {'model':'<synthetic>', 'content':[{'type':'text','text':'Not logged in'}]}},
                             {'type':'result', 'is_error':True, 'result':'Not logged in', 'modelUsage':{}}])
        self.assertTrue(parsed['is_error'])
        self.assertEqual(parsed['models'], [])
        self.assertIn('authentication_failed', parsed['error_details'])
        self.assertIsNone(quota_fallback_reason(parsed, 'claude-fable-5-1'))



class FallbackIntegrationTest(unittest.TestCase):
    def test_usage_billing_signals_stop_before_opus(self):
        fixture = Path(__file__).parent / 'fixtures/claude-session-quota.jsonl'
        for changes in ({'spend_cap':'reached'}, {'credit_balance':0}, {'unknown':None}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); (root/'prompt').write_text('synthetic')
                events = [json.loads(line) for line in fixture.read_text().splitlines()]
                events[0]['api_error_params'].update(changes)
                args = SimpleNamespace(command=['claude','--model','claude-fable-5-1'], cwd=str(root),
                    timeout=5, stdin=str(root/'prompt'), stdout=str(root/'result.raw.json'),
                    stderr=str(root/'result.err'), primary_model='claude-fable-5-1', fallback_model='claude-opus-5-5')
                def once(attempt):
                    Path(attempt.stdout).write_text('\n'.join(json.dumps(e) for e in events))
                    Path(attempt.stderr).write_text('')
                    return {'exit_code':1, 'timeout':False, 'process_reaped':True,
                            'group_cleanup_complete':True, 'cleanup_errors':[]}
                with patch.object(run_cli, 'run_once', side_effect=once) as execute:
                    result = run_cli.run(args)
                self.assertEqual(execute.call_count, 1)
                self.assertEqual(result['exit_code'], 1)
                self.assertFalse(result['fallback_used'])
                self.assertFalse(result['attempts'][0]['fallback_eligible'])
                self.assertEqual(json.loads(Path(result['attempts'][0]['stdout']).read_text().splitlines()[0])['api_error_params'], events[0]['api_error_params'])

    def test_quota_switch_preserves_both_attempts_and_exact_models(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/'prompt').write_text('same synthetic question')
            cli = root/'claude'
            cli.write_text(f'#!{sys.executable}\n' + '''import json,sys
model=sys.argv[sys.argv.index('--model')+1]
question=sys.stdin.read()
if model.startswith('claude-fable'):
 print(json.dumps({'type':'assistant','error':'rate_limit','api_error_kind':'usage_limit_reached','is_api_error_message':True,'api_error_status':429,'api_error_origin':'first_party','api_error_params':{'rate_limit_info':{'status':'rejected','rateLimitType':'seven_day','isUsingOverage':False}},'message':{'content':[{'type':'text','text':'original quota failure'}]}}))
 print(json.dumps({'type':'result','is_error':True,'result':'original quota failure','modelUsage':{}}))
 sys.exit(1)
print(json.dumps({'type':'result','is_error':False,'result':question,'modelUsage':{model:{}}}))
''')
            cli.chmod(0o755)
            args=SimpleNamespace(command=[str(cli),'--model','claude-fable-5-1'],cwd=str(root),timeout=5,
                                 stdin=str(root/'prompt'),stdout=str(root/'result.raw.json'),stderr=str(root/'result.err'),
                                 primary_model='claude-fable-5-1',fallback_model='claude-opus-5-5')
            result=run_cli.run(args)
            self.assertEqual(result['exit_code'],0,result)
            self.assertTrue(result['fallback_used'])
            first,second=result['attempts']
            self.assertEqual(first['exit_code'],1)
            self.assertEqual(second['model_observed'],['claude-opus-5-5'])
            self.assertEqual(first['prompt_sha256'],second['prompt_sha256'])
            self.assertIn('original quota failure',Path(first['stdout']).read_text())
            self.assertEqual(Path(second['out']).read_text(),'same synthetic question')
            import os
            env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH'])
            asked=subprocess.run(['bash',str(SCRIPTS/'ask.sh'),'claude','--prompt-file',str(root/'prompt'),
                                  '--out-dir',str(root/'asked'),'--cwd',str(root),'--timeout','5'],
                                 capture_output=True,text=True,env=env,timeout=12)
            self.assertEqual(asked.returncode,0,asked.stderr)
            meta=json.loads(Path(asked.stdout.strip()).read_text())
            self.assertIsNone(meta['model_requested'])
            self.assertEqual(meta['model_primary'],'claude-fable-5-1')
            self.assertEqual(meta['model_attempted'],'claude-opus-5-5')
            self.assertTrue(meta['approved_fallback'])
            self.assertFalse(meta['model_mismatch'])

    def test_quota_with_cleanup_denial_does_not_start_second_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'prompt').write_text('question')
            args=SimpleNamespace(command=['synthetic','--model','claude-fable-5-1'],cwd=temp,timeout=5,
                                 stdin=str(root/'prompt'),stdout=str(root/'result.raw.json'),stderr=str(root/'result.err'),
                                 primary_model='claude-fable-5-1',fallback_model='claude-opus-5-5')
            def failed(attempt):
                events=[{'type':'assistant','api_error_kind':'usage_limit_reached','is_api_error_message':True,'api_error_status':429,'api_error_origin':'first_party','api_error_params':{'rate_limit_info':{'status':'rejected','rateLimitType':'seven_day','isUsingOverage':False}}},
                        {'type':'result','is_error':True,'result':'quota failure','modelUsage':{}}]
                Path(attempt.stdout).write_text('\n'.join(json.dumps(e) for e in events));Path(attempt.stderr).write_text('original error')
                return {'exit_code':1,'timeout':False,'process_reaped':True,'group_cleanup_complete':None,'cleanup_errors':[{'error':'PermissionError'}]}
            with patch.object(run_cli,'run_once',side_effect=failed) as invoked:
                result=run_cli.run(args)
            self.assertEqual(invoked.call_count,1)
            self.assertFalse(result['fallback_used'])


class FrozenPromptTest(unittest.TestCase):
    def test_missing_input_returns_state_without_secondary_hash_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            args=SimpleNamespace(command=['synthetic','--model','claude-fable-5-1'],cwd=temp,timeout=5,
                                 stdin=str(root/'missing'),stdout=str(root/'result.raw.json'),stderr=str(root/'result.err'),
                                 primary_model='claude-fable-5-1',fallback_model='claude-opus-5-5')
            result=run_cli.run(args)
            self.assertIn('FileNotFoundError',result['runner_error'])
            self.assertEqual(result['exit_code'],1)
            json.dumps(result)

    def test_prompt_mutation_does_not_change_second_input_or_recorded_hash(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);prompt=root/'prompt';prompt.write_text('original question')
            args=SimpleNamespace(command=['synthetic','--model','claude-fable-5-1'],cwd=temp,timeout=5,
                                 stdin=str(prompt),stdout=str(root/'result.raw.json'),stderr=str(root/'result.err'),
                                 primary_model='claude-fable-5-1',fallback_model='claude-opus-5-5')
            received=[]
            def attempt(a):
                received.append(a.input_bytes)
                prompt.write_text('changed question')
                if len(received)==1:
                    events=[{'type':'assistant','api_error_kind':'usage_limit_reached','is_api_error_message':True,'api_error_status':429,'api_error_origin':'first_party','api_error_params':{'rate_limit_info':{'status':'rejected','rateLimitType':'seven_day','isUsingOverage':False}}},
                            {'type':'result','is_error':True,'result':'quota failure','modelUsage':{}}]
                else:
                    events=[{'type':'result','result':a.input_bytes.decode(),'modelUsage':{'claude-opus-5-5':{}}}]
                Path(a.stdout).write_text('\n'.join(json.dumps(e) for e in events));Path(a.stderr).write_text('')
                return {'exit_code':1 if len(received)==1 else 0,'timeout':False,'process_reaped':True,'group_cleanup_complete':True,'cleanup_errors':[]}
            with patch.object(run_cli,'run_once',side_effect=attempt):
                result=run_cli.run(args)
            self.assertEqual(received,[b'original question',b'original question'])
            digest=hashlib.sha256(b'original question').hexdigest()
            self.assertEqual([a['prompt_sha256'] for a in result['attempts']],[digest,digest])

if __name__ == '__main__':
    unittest.main()
