import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/cross-check/scripts'))
import run_cli
import collect_meta
import cross_check_capture as capture
from claude_events import read_claude, quota_fallback_reason
from cross_check_capture import public_event


class FableAvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = ROOT / 'tests/fixtures/claude-fable-model-credits.jsonl'

    def events(self):
        return [json.loads(line) for line in self.fixture.read_text().splitlines()]

    def parse(self, events):
        raw = self.root / 'events.jsonl'
        raw.write_text('\n'.join(json.dumps(event) for event in events))
        return read_claude(str(raw))

    def test_observed_first_party_model_credits_has_distinct_reason(self):
        for model in ('claude-fable-5-1', 'claude-fable-5'):
            parsed = self.parse(self.events())
            self.assertEqual(quota_fallback_reason(parsed, model),
                             'fable_request_model_requires_usage_credits')
            self.assertEqual(parsed['error_kind'], 'model_requires_usage_credits')
            self.assertIsNone(parsed['rate_limit_info'])
            self.assertEqual(parsed['models'], [])

    def test_incomplete_attestation_and_other_models_never_switch(self):
        for changes in ({'api_error_origin': 'third_party'}, {'api_error_status': 401},
                        {'api_error_status': True}, {'is_api_error_message': False},
                        {'api_error': 'authentication_failed'}, {'error': 'credit_balance_exhausted'},
                        {'error': 'consent_required'}, {'api_error': 'unknown'}):
            with self.subTest(changes=changes):
                events = self.events(); events[0].update(changes)
                self.assertIsNone(quota_fallback_reason(self.parse(events), 'claude-fable-5-1'))
        for key in ('api_error_origin', 'api_error_status', 'is_api_error_message', 'api_error'):
            events = self.events(); events[0].pop(key)
            self.assertIsNone(quota_fallback_reason(self.parse(events), 'claude-fable-5-1'))
        parsed = self.parse(self.events())
        for model in ('claude-opus-5-5', 'fable', 'unknown', ''):
            self.assertIsNone(quota_fallback_reason(parsed, model))
        events = self.events(); events[-1]['modelUsage'] = {'unknown-model': {}}
        self.assertIsNone(quota_fallback_reason(self.parse(events), 'claude-fable-5-1'))

    def test_billing_policy_consent_and_unknown_params_fail_closed(self):
        for params in ({'credit_balance': 0}, {'requires_consent': True},
                       {'organization_policy': 'disabled'}, {'spend_cap': 'reached'},
                       {'rate_limit_info': {'overageDisabledReason': 'org_level_disabled_until'}},
                       {'unknown': None}):
            with self.subTest(params=params):
                events = self.events(); events[0]['api_error_params'] = params
                self.assertIsNone(quota_fallback_reason(self.parse(events), 'claude-fable-5-1'))
        events = self.events(); events[0]['api_error_params'] = {}
        self.assertEqual(quota_fallback_reason(self.parse(events), 'claude-fable-5-1'),
                         'fable_request_model_requires_usage_credits')

    def test_alias_and_session_conflicts_remain_ineligible(self):
        events = self.events(); events[0]['api_error_kind'] = 'usage_limit_reached'
        self.assertFalse(self.parse(events)['valid'])
        events = self.events(); events[-1]['session_id'] = 'other-session'
        self.assertIsNone(quota_fallback_reason(self.parse(events), 'claude-fable-5-1'))
        events = self.events()
        events.insert(1, {'type': 'assistant', 'session_id': events[0]['session_id'],
                          'api_error_params': {'consent_required': True}})
        events[0]['api_error_params'] = {}
        self.assertFalse(self.parse(events)['valid'])

    def test_missing_or_blank_terminal_session_is_ineligible_after_capture(self):
        for sid in (None, '', ' ', '\t', '\n'):
            with self.subTest(sid=sid):
                events=self.events()
                for event in events:event['session_id']=sid
                parsed=self.parse([public_event(event) for event in events])
                self.assertIsNone(quota_fallback_reason(parsed,'claude-fable-5-1'))
        events=self.events()
        for event in events:event.pop('session_id',None)
        self.assertIsNone(quota_fallback_reason(self.parse(events),'claude-fable-5-1'))
        events=self.events()
        events.insert(0,{'type':'system','subtype':'init','model':'claude-fable-5-1',
                         'session_id':events[0]['session_id']})
        events[-1].pop('session_id')
        self.assertIsNone(quota_fallback_reason(
            self.parse([public_event(event) for event in events]),'claude-fable-5-1'))

    def observed_rate_event(self, sid):
        return {"type": "rate_limit_event", "uuid": "fixture-event", "session_id": sid,
                "rate_limit_info": {"status": "rejected", "resetsAt": 100,
                    "rateLimitType": "seven_day_overage_included", "overageStatus": "rejected",
                    "overageDisabledReason": "org_level_disabled", "isUsingOverage": False,
                    "unifiedWindows": {"five_hour": {"utilization": 0.06, "resetsAt": 100, "status": "allowed"},
                        "seven_day": {"utilization": 0.98, "resetsAt": 100, "status": "allowed_warning", "surpassedThreshold": 0.75},
                        "seven_day_overage_included": {"utilization": 1, "resetsAt": 100, "status": "rejected", "surpassedThreshold": 1}}}}

    def test_rate_event_preserved_as_distinct_diagnostic_and_credits_allowed(self):
        events = self.events(); rate = self.observed_rate_event(events[0]['session_id'])
        events.insert(1, rate); parsed = self.parse([public_event(e) for e in events])
        self.assertEqual(parsed['rate_limit_events'], [rate])
        self.assertIsNone(parsed['rate_limit_info'])
        self.assertTrue(parsed['rate_limit_events_safe'])
        self.assertEqual(quota_fallback_reason(parsed, 'claude-fable-5-1'), 'fable_request_model_requires_usage_credits')
        snapshot = self.observed_rate_event(events[0]['session_id'])
        snapshot['rate_limit_info']['status'] = 'allowed_warning'
        events.insert(1, snapshot)
        self.assertEqual(quota_fallback_reason(self.parse(events), 'claude-fable-5-1'), 'fable_request_model_requires_usage_credits')

    def test_rate_event_unknown_billing_session_or_malformed_blocks_only_fallback(self):
        variants = [({'overageDisabledReason': 'org_level_disabled_until'}, None),
                    ({'spend_cap': 1}, None), ({'balance': 0}, None),
                    ({'consent': True}, None), ({'unknown': None}, None),
                    ({'isUsingOverage': True}, None), ({'status': []}, None),
                    ({'rate_limit_type': 'other'}, None), ({'unifiedWindows': {'unknown': {}}}, None),
                    ({}, 'other-session'), ({}, ''), ({}, 3)]
        for changes, sid in variants:
            with self.subTest(changes=changes, sid=sid):
                events = self.events(); rate = self.observed_rate_event(events[0]['session_id'])
                rate['rate_limit_info'].update(changes)
                if sid is not None: rate['session_id'] = sid
                events.insert(1, rate); parsed = self.parse(events)
                self.assertEqual(parsed['rate_limit_events'], [rate])
                self.assertFalse(parsed['rate_limit_events_safe'])
                self.assertIsNone(quota_fallback_reason(parsed, 'claude-fable-5-1'))
        for info in (None, [], 'invalid'):
            events = self.events(); rate = self.observed_rate_event(events[0]['session_id'])
            rate['rate_limit_info'] = info; events.insert(1, rate)
            self.assertFalse(self.parse(events)['rate_limit_events_safe'])
        events = [{'type': 'result', 'session_id': 'ok', 'result': 'answer', 'is_error': False,
                   'modelUsage': {'claude-opus-5-5': {}}},
                  {'type': 'rate_limit_event', 'session_id': 'other', 'rate_limit_info': {'unknown': 1}}]
        parsed = self.parse(events)
        self.assertTrue(parsed['valid']); self.assertEqual(parsed['body'], 'answer')
        self.assertFalse(parsed['rate_limit_events_safe'])

    def test_timestamped_capture_preserves_unknown_rate_fields_and_decision(self):
        events = self.events(); rate = self.observed_rate_event(events[0]['session_id'])
        for changes, expected in (({}, True), ({'billing_consent_required': True}, False)):
            with self.subTest(changes=changes):
                event = {**rate, **changes}; captured = public_event(event); captured['captured_at'] = 123.5
                parsed = self.parse([events[0], captured, events[-1]])
                self.assertEqual(parsed['rate_limit_events'], [event])
                self.assertEqual(parsed['rate_limit_events_safe'], expected)
                self.assertEqual(quota_fallback_reason(parsed, 'claude-fable-5-1') is not None, expected)
        for timestamp in ('invalid', None, 10**400):
            captured = public_event(rate); captured['captured_at'] = timestamp
            self.assertFalse(self.parse([events[0], captured, events[-1]])['rate_limit_events_safe'])
        for target in ('resetsAt', 'window'):
            event = self.observed_rate_event(events[0]['session_id'])
            if target == 'window': event['rate_limit_info']['unifiedWindows']['five_hour']['utilization'] = 10**400
            else: event['rate_limit_info'][target] = 10**400
            parsed = self.parse([events[0], public_event(event), events[-1]])
            self.assertTrue(parsed['valid']); self.assertFalse(parsed['rate_limit_events_safe'])

    def test_public_capture_and_meta_preserve_original_model_error(self):
        events = [public_event(event) for event in self.events()]
        parsed = self.parse(events)
        self.assertEqual(quota_fallback_reason(parsed, 'claude-fable-5-1'),
                         'fable_request_model_requires_usage_credits')
        raw = self.root / 'events.jsonl'
        original = raw.read_bytes()
        args = collect_meta.parse(['--side', 'claude', '--model', 'claude-fable-5-1',
            '--effort', 'high', '--exit-code', '1', '--timeout', '300', '--started-at', 's',
            '--ended-at', 'e', '--elapsed', '1', '--prompt-file', str(self.root/'prompt'),
            '--cwd', str(self.root), '--out', str(self.root/'out'), '--err', str(self.root/'err'),
            '--raw', str(raw), '--meta', str(self.root/'meta')])
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta['status']), (1, 'error'))
        self.assertEqual(meta['claude_error']['error_kind'], 'model_requires_usage_credits')
        self.assertIsNone(meta['model_observed'])
        self.assertEqual(raw.read_bytes(), original)

    def policy_args(self):
        prompt = self.root / 'prompt'; prompt.write_bytes(b'fixed question')
        return SimpleNamespace(command=['claude', '--model', 'claude-fable-5-1'],
            cwd=str(self.root), stdin=str(prompt), stdout=str(self.root/'peer.raw.json'),
            stderr=str(self.root/'peer.err'), timeout=300,
            primary_model='claude-fable-5-1', fallback_model='claude-opus-5-5')

    def run_policy(self, *, opus_error=False):
        args = self.policy_args(); received=[]
        def once(attempt):
            received.append((attempt.command[-1], attempt.input_bytes, attempt.deadline))
            if len(received) == 1 or opus_error:
                data=self.fixture.read_bytes(); code=1
            else:
                data=json.dumps({'type':'result','session_id':'opus-result','result':'answer',
                                 'is_error':False,'modelUsage':{'claude-opus-5-5':{}}}).encode(); code=0
            Path(attempt.stdout).write_bytes(data);Path(attempt.stderr).write_bytes(b'')
            return {'exit_code':code,'timeout':False,'process_reaped':True,
                    'group_cleanup_complete':True,'cleanup_errors':[]}
        with patch.object(run_cli, 'run_once', side_effect=once): result=run_cli.run(args)
        return result, received

    def test_model_credits_switches_once_with_distinct_metadata_and_frozen_input(self):
        result, received = self.run_policy()
        self.assertEqual([call[0] for call in received], ['claude-fable-5-1', 'claude-opus-5-5'])
        self.assertEqual(received[0][1:], received[1][1:])
        self.assertEqual(result['exit_code'], 0)
        first, second = result['attempts']
        self.assertTrue(first['fallback_eligible'])
        self.assertFalse(first['quota_eligible'])
        self.assertEqual(first['fallback_reason'], 'fable_request_model_requires_usage_credits')
        self.assertEqual(first['error_kind'], 'model_requires_usage_credits')
        self.assertEqual(Path(first['stdout']).read_bytes(), self.fixture.read_bytes())
        self.assertEqual(second['model_requested'], 'claude-opus-5-5')
        self.assertFalse(second['fallback_eligible'])
        args = collect_meta.parse(['--side','claude','--model','claude-fable-5-1',
            '--effort','high','--exit-code','0','--runner-result',json.dumps(result),
            '--timeout','300','--started-at','s','--ended-at','e','--elapsed','1',
            '--prompt-file',str(self.root/'prompt'),'--cwd',str(self.root),
            '--out',str(self.root/'collected.out'),'--err',str(self.root/'peer.err'),
            '--raw',str(self.root/'peer.raw.json'),'--meta',str(self.root/'meta')])
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta['status']), (0, 'ok'))
        self.assertTrue(meta['approved_fallback'])
        self.assertEqual(meta['fallback_reason'], 'fable_request_model_requires_usage_credits')
        self.assertEqual(meta['attempts'][0]['error_kind'], 'model_requires_usage_credits')

    def test_opus_model_credits_stops_after_second_attempt(self):
        result, received = self.run_policy(opus_error=True)
        self.assertEqual(len(received), 2)
        self.assertEqual(result['exit_code'], 1)
        self.assertTrue(result['fallback_used'])
        self.assertFalse(result['attempts'][-1]['fallback_eligible'])

    def test_explicit_opus_does_not_enter_fallback_policy(self):
        args = self.policy_args();args.command[-1]='claude-opus-5-5';args.fallback_model=None
        with patch.object(run_cli, 'run_once', return_value={'exit_code':0}) as once, \
             patch.object(run_cli, 'quota_fallback_reason') as classification:
            self.assertEqual(run_cli.run(args), {'exit_code':0})
        once.assert_called_once_with(args)
        classification.assert_not_called()

    def test_caller_model_credits_switches_only_before_any_tool(self):
        for after_tool in (False, True):
            with self.subTest(after_tool=after_tool):
                folder=self.root/str(after_tool);folder.mkdir()
                prompt=folder/'prompt';prompt.write_text('same question')
                script=folder/'fake.py';count=folder/'calls'
                script.write_text(
                    "import json,sys\nfrom pathlib import Path\n"
                    f"count=Path({str(count)!r});n=int(count.read_text())+1 if count.exists() else 1;count.write_text(str(n))\n"
                    f"if n==1:\n events=[json.loads(s) for s in Path({str(self.fixture)!r}).read_text().splitlines()]\n"
                    + (" events.insert(0,{'type':'assistant','message':{'content':[{'type':'tool_use','name':'Skill','id':'native','input':{}}]}})\n" if after_tool else '')
                    + " for event in events:print(json.dumps(event),flush=True)\n sys.exit(1)\n"
                    + "print(json.dumps({'type':'result','session_id':'opus-caller','is_error':False,'result':'answer','modelUsage':{'claude-opus-5-5':{}}}),flush=True)\n")
                result=capture.capture_policy([sys.executable,str(script),'--model','claude-fable-5-1'],
                    cwd=folder,prompt_file=prompt,output_dir=folder/'capture',timeout=5,
                    primary_model='claude-fable-5-1')
                self.assertEqual(int(count.read_text()),1 if after_tool else 2)
                self.assertEqual(result['policy_exit_code'],1 if after_tool else 0)
                self.assertTrue(result['cleanup_complete'])
                self.assertEqual(result['collector_errors'],[])


if __name__ == '__main__':
    unittest.main()
