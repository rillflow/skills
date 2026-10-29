import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import cross_check_capture as capture


class CaptureTest(unittest.TestCase):
    def test_public_capture_preserves_sdk_quota_and_alias_conflicts(self):
        path = Path(__file__).parent / 'fixtures/claude-session-quota.jsonl'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        event = events[0]
        event.update(apiError='authentication_failed', apiErrorParams={'rate_limit_info': {}},
                     isApiErrorMessage=False, private_diagnostic='must not escape')
        public = capture.public_event(event)
        for field in ('api_error', 'api_error_params', 'api_error_status', 'api_error_origin',
                      'is_api_error_message', 'apiError', 'apiErrorParams', 'isApiErrorMessage'):
            self.assertEqual(public.get(field), event[field])
        self.assertNotIn('private_diagnostic', public)

    def test_observed_quota_survives_public_capture_and_parser(self):
        from claude_events import read_claude, quota_fallback_reason
        path = Path(__file__).parent / 'fixtures/claude-session-quota.jsonl'
        events = [capture.public_event(json.loads(line)) for line in path.read_text().splitlines()]
        self.root.joinpath('captured.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        parsed = read_claude(str(self.root / 'captured.jsonl'))
        self.assertEqual(quota_fallback_reason(parsed, 'claude-fable-5-1'),
                         'fable_request_usage_limit')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prompt = self.root / 'prompt.txt'
        self.prompt.write_text('合成入力')

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, code, timeout=3):
        return capture.capture([sys.executable, '-u', '-c', code], cwd=self.root,
                               prompt_file=self.prompt, output_dir=self.root / 'out',
                               timeout=timeout)

    def events(self):
        return [json.loads(x) for x in (self.root / 'out/events.jsonl').read_text().splitlines()]

    def test_non_dict_messages_and_permission_denied_do_not_stop_cli(self):
        shapes = [{'content': [{'type': 'text', 'text': '公開'}]}, 'permission denied', None,
                  [{'type': 'text', 'text': '公開'}, {'type': 'thinking', 'thinking': '非公開'}]]
        events = [{'type': 'system', 'subtype': 'permission_denied', 'message': x} for x in shapes]
        code = 'import json\n'
        code += f'events={events!r}\n'
        code += 'for e in events: print(json.dumps(e), flush=True)\n'
        code += "print(json.dumps({'type':'result','result':'完了'}), flush=True)\n"
        result = self.run_cli(code)
        self.assertEqual(result['caller_exit_code'], 0)
        self.assertEqual(result['collector_errors'], [])
        self.assertEqual(len(self.events()), 5)
        self.assertNotIn('非公開', (self.root / 'out/events.jsonl').read_text())
        self.assertEqual(result['result']['result'], '完了')

    def test_public_block_allowlist_removes_hidden_blocks(self):
        event = {'type': 'assistant', 'thinking': 'secret', 'message': {'role': 'assistant',
                 'content': [{'type': 'thinking', 'thinking': 'secret'},
                             {'type': 'redacted_thinking', 'data': 'secret'},
                             {'type': 'text', 'text': 'visible'},
                             {'type': 'unknown', 'payload': 'secret'}]}}
        result = capture.public_event(event)
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['message']['content'], [{'type': 'text', 'text': 'visible'}])

    def test_parse_and_schema_errors_do_not_replace_caller_exit(self):
        result = self.run_cli("import sys\nprint('not JSON',flush=True)\nprint('[]',flush=True)\nsys.exit(7)")
        self.assertEqual(result['caller_exit_code'], 7)
        self.assertFalse(result['timeout'])
        self.assertEqual(len(result['collector_errors']), 2)
        self.assertEqual([x['type'] for x in self.events()], ['capture_parse_error', 'capture_schema_error'])

    def test_readonly_bash_event_is_observed_without_command_allowlist(self):
        event = {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': 'read', 'name': 'Bash',
             'input': {'command': 'ls -la && cat runtime.json; cat references/claude-code.md'}}]}}
        marker = self.root / 'continued'
        code = f"import json\nfrom pathlib import Path\nprint(json.dumps({event!r}),flush=True)\nPath({str(marker)!r}).write_text('yes')\n"
        result = self.run_cli(code)
        self.assertEqual(result['caller_exit_code'], 0)
        self.assertEqual(result['collector_errors'], [])
        self.assertTrue(marker.exists())
        self.assertEqual(self.events()[0]['message'], event['message'])

    def test_sink_error_drains_and_reaps_caller(self):
        original = capture.append_event
        count = 0
        def fail_once(sink, event):
            nonlocal count
            count += 1
            if count == 1:
                raise OSError('synthetic sink failure')
            original(sink, event)
        marker = self.root / 'finished'
        code = ("from pathlib import Path\nprint('{}',flush=True)\n"
                f"Path({str(marker)!r}).write_text('done')\nprint('{{}}',flush=True)\n")
        with patch.object(capture, 'append_event', side_effect=fail_once):
            result = self.run_cli(code)
        self.assertEqual(result['caller_exit_code'], 0)
        self.assertTrue(marker.exists())
        self.assertIn('synthetic sink failure', result['collector_errors'][0])

    def test_cleanup_permission_error_keeps_original_error_and_caller_exit(self):
        marker = self.root / 'caller-finished'
        code = f"from pathlib import Path\nimport sys\nPath({str(marker)!r}).write_text('done')\nsys.exit(7)\n"
        def fail_select(_timeout):
            end = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < end:
                time.sleep(.01)
            raise OSError('synthetic observer failure')
        with patch.object(capture.selectors.DefaultSelector, 'select', side_effect=fail_select), \
             patch.object(capture.runner, 'kill_group', side_effect=PermissionError('synthetic group denial')):
            result = self.run_cli(code)
        saved = json.loads((self.root / 'out/capture.json').read_text())
        self.assertEqual(saved['caller_exit_code'], 7)
        self.assertIn('synthetic observer failure', result['collector_errors'][0])
        self.assertIn('synthetic group denial', result['cleanup_errors'][0])
        self.assertFalse(result['cleanup_complete'])
        self.assertGreater(result['process_id'], 0)

    def test_cancel_permission_error_reaps_direct_child_and_records_failure(self):
        for signum in capture.SIGNALS:
            with self.subTest(signal=signum):
                case = self.root / str(signum)
                case.mkdir()
                code = ("import os,time,json\nfrom pathlib import Path\n"
                        f"Path({str(case/'ready')!r}).write_text(str(os.getpid()))\n"
                        "print(json.dumps({'type':'system','message':'ready'}),flush=True)\n"
                        "time.sleep(15)\n")
                adapter = case / 'adapter.py'
                adapter.write_text(
                    f"import sys,os,signal,json\nsys.path.insert(0,{str(Path(capture.__file__).parent)!r})\n"
                    "from pathlib import Path\nimport cross_check_capture as c\n"
                    "def deny_group(pid,sig):\n raise PermissionError('synthetic group denial')\n"
                    "c.runner.kill_group=deny_group\n"
                    f"r=c.capture([sys.executable,'-u','-c',{code!r}],cwd={str(case)!r},"
                    f"prompt_file={str(self.prompt)!r},output_dir={str(case/'out')!r},timeout=10)\n"
                    "alive=False\n"
                    "try:\n"
                    " pid=r['process_id']\n"
                    " alive=os.waitpid(pid,os.WNOHANG)==(0,0)\n"
                    " if alive:\n  os.kill(pid,signal.SIGKILL)\n  os.waitpid(pid,0)\n"
                    "except ChildProcessError:\n pass\n"
                    f"Path({str(case/'probe.json')!r}).write_text(json.dumps({{'direct_child_alive_after_capture':alive}}))\n")
                process = subprocess.Popen([sys.executable, str(adapter)])
                try:
                    end = time.monotonic() + 3
                    while time.monotonic() < end:
                        events = case/'out/events.jsonl'
                        if (case/'ready').exists() and events.exists() and events.stat().st_size:
                            break
                        time.sleep(.01)
                    self.assertTrue((case/'ready').exists())
                    process.send_signal(signum)
                    process.wait(timeout=3)
                    self.assertEqual(process.returncode, 0)
                    result = json.loads((case/'out/capture.json').read_text())
                    self.assertEqual(result['cancel_signal'], signum)
                    self.assertFalse(result['timeout'])
                    self.assertEqual(result['caller_returncode'], -signal.SIGKILL)
                    self.assertEqual(result['caller_exit_code'], 128 + signal.SIGKILL)
                    self.assertFalse(result['cleanup_complete'])
                    self.assertIn('synthetic group denial', result['cleanup_errors'][0])
                    self.assertIn('synthetic group denial', result['collector_errors'][0])
                    self.assertFalse(json.loads((case/'probe.json').read_text())['direct_child_alive_after_capture'])
                    events = [json.loads(line) for line in (case/'out/events.jsonl').read_text().splitlines()]
                    self.assertEqual(events[0]['message'], 'ready')
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    def test_cancel_during_event_save_stops_buffered_and_eof_collection(self):
        for mode in ('buffered', 'eof'):
            for signum in capture.SIGNALS:
                with self.subTest(mode=mode, signal=signum):
                    events = [{'type': 'system', 'message': x} for x in ('one', 'two', 'three')]
                    payload = '\n'.join(json.dumps(e) for e in events) + '\n'
                    if mode == 'eof':
                        payload = json.dumps(events[0])
                    code = f"import os,time\nos.write(1,{payload.encode()!r})\n"
                    if mode == 'eof':
                        code += 'os.close(1)\n'
                    code += 'time.sleep(1.2)\n'
                    original = capture.append_event
                    calls = 0
                    cancel_at = 2 if mode == 'buffered' else 1
                    def cancel_during_save(sink, event):
                        nonlocal calls
                        calls += 1
                        if calls == cancel_at:
                            os.kill(os.getpid(), signum)
                        original(sink, event)
                    with patch.object(capture, 'append_event', side_effect=cancel_during_save), \
                         patch.object(capture.runner, 'kill_group', side_effect=PermissionError('synthetic group denial')):
                        result = self.run_cli(code)
                    self.assertEqual(result['cancel_signal'], signum, result)
                    self.assertFalse(result['timeout'])
                    self.assertEqual(result['caller_returncode'], -signal.SIGKILL)
                    self.assertEqual(result['caller_exit_code'], 128 + signal.SIGKILL)
                    self.assertFalse(result['cleanup_complete'])
                    self.assertIn('synthetic group denial', result['cleanup_errors'][0])
                    self.assertIn('synthetic group denial', result['collector_errors'][0])
                    self.assertEqual(calls, cancel_at)
                    self.assertEqual([e['message'] for e in self.events()],
                                     ['one'] if mode == 'buffered' else [])

    def test_policy_capture_preserves_native_events_and_actual_cli_exits(self):
        for mode in ('normal', 'auth', 'quota', 'quota_after_tool'):
            with self.subTest(mode=mode):
                case = self.root / mode
                case.mkdir()
                script = case / 'fake_cli.py'
                script.write_text('import json,sys\n' + f'mode={mode!r}\n' + '''model=sys.argv[sys.argv.index('--model')+1]
print(json.dumps({'type':'system','subtype':'init','model':model,'skills':['cross-check','ideation']}),flush=True)
if mode=='normal' or model=='claude-opus-5-5':
 print(json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','id':'native','name':'Skill','input':{'skill':'cross-check'}}]}}),flush=True)
 print(json.dumps({'type':'result','result':'native answer','is_error':False,'modelUsage':{model:{}}}),flush=True)
 sys.exit(0)
if mode=='auth':
 print(json.dumps({'type':'assistant','error':'authentication_failed','message':{'model':'<synthetic>','content':[{'type':'text','text':'Not logged in'}]}}),flush=True)
else:
 if mode=='quota_after_tool':
  print(json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','id':'peer','name':'Bash','input':{'command':'synthetic peer call'}}]}}),flush=True)
 print(json.dumps({'type':'assistant','api_error_kind':'usage_limit_reached','is_api_error_message':True,'api_error_status':429,'api_error_origin':'first_party','api_error_params':{'rate_limit_info':{'status':'rejected','rateLimitType':'seven_day','isUsingOverage':False}}}),flush=True)
print(json.dumps({'type':'result','result':'native failure','is_error':True,'modelUsage':{}}),flush=True)
sys.exit(1)
''')
                result = capture.capture_policy([sys.executable, str(script), '--model', 'claude-fable-5-1'],
                    cwd=case, prompt_file=self.prompt, output_dir=case/'out', timeout=5,
                    primary_model='claude-fable-5-1')
                attempts = result['policy_result']['attempts']
                self.assertEqual(len(attempts), 2 if mode == 'quota' else 1)
                self.assertEqual(result['caller_exit_code'], 0 if mode in ('normal','quota') else 1)
                self.assertEqual(result['policy_exit_code'], result['caller_exit_code'])
                self.assertTrue(result['process_reaped'])
                self.assertTrue(result['cleanup_complete'])
                self.assertEqual(result['collector_errors'], [])
                for attempt in attempts:
                    events = [json.loads(x) for x in Path(attempt['public_events']).read_text().splitlines()]
                    self.assertEqual(events[0]['subtype'], 'init')
                    self.assertEqual(events[0]['skills'], ['cross-check','ideation'])
                self.assertEqual(result['result']['type'], 'result')
                self.assertNotIn('exit_code', result['result'])

    def test_full_body_and_chunked_numbered_readback(self):
        body = '# 判断\n\n結論: B\n根拠: 原子性\n  2件の条件\n\n末尾\n'
        parts = ['1→# 判断\n2→\n3→結論: B', '4→根拠: 原子性\n5→  2件の条件\n6→\n7→末尾']
        result = capture.readback_evidence(body, parts, ['event:1', 'event:2'], numbered=True)
        self.assertTrue(result['complete'])
        self.assertEqual(result['total_lines'], 7)
        self.assertEqual(result['references'], ['event:1', 'event:2'])

    def test_first_five_lines_and_missing_conclusion_are_incomplete(self):
        body = '# 初期判断\n\n資料\n版\n方法\n\n結論: B\n\n根拠: 原子性\n'
        for shown in ['\n'.join(body.splitlines()[:5]), body.replace('結論: B\n', '')]:
            with self.subTest(shown=shown):
                self.assertFalse(capture.readback_evidence(body, [shown], ['event:1'])['complete'])

    def test_body_numbers_indentation_and_blank_lines_are_not_erased(self):
        body = '123→本文\n  2件\n\n末尾\n'
        self.assertTrue(capture.readback_evidence(body, [body], ['event:1'])['complete'])
        for shown in ['本文\n  2件\n\n末尾', '123→本文\n2件\n\n末尾', '123→本文\n  2件\n末尾']:
            self.assertFalse(capture.readback_evidence(body, [shown], ['event:1'])['complete'])

    def test_tab_numbered_display_preserves_body_and_rejects_first_five(self):
        body = '# 判断\n\n資料\n版\n方法\n\n123\t結論: B\n  根拠: 2件\n\n末尾\n'
        display = '\n'.join(f'{i}\t{line}' for i, line in enumerate(body.splitlines(), 1)) + '\n11\t'
        result = capture.readback_evidence(body, [display], ['event:44'], numbered=True)
        self.assertTrue(result['complete'])
        self.assertEqual((result['total_lines'], result['returned_lines']), (10, 11))
        partial = '\n'.join(display.splitlines()[:5])
        self.assertFalse(capture.readback_evidence(body, [partial], ['event:44'], numbered=True)['complete'])
        self.assertTrue(capture.readback_evidence(body, [body], ['raw'])['complete'])

    def test_peer_inventory_requires_command_review_even_for_one_pair(self):
        peer = self.root / 'peer';peer.mkdir()
        commands = ['cat ask.sh', 'bash ./ask.sh codex', 'codex exec -']
        events = [{'message': {'content': [{'type': 'tool_use', 'name': 'Bash',
                   'input': {'command': command}}]}} for command in commands]
        for count in [0, 1, 2]:
            if count:
                (peer / f'run-{count}.meta.json').write_text('{}')
                (peer / f'run-{count}.out').write_text('応答')
            result = capture.peer_evidence(events, peer)
            self.assertEqual(len(result['meta_prefixes']), count)
            self.assertEqual(len(result['bash_commands']), 3)
            self.assertIsNone(result['actual_calls'])
        (peer / 'run-2.out').unlink()
        self.assertFalse(capture.peer_evidence(events, peer)['paired'])

    def test_cli_exit_124_is_not_collector_timeout(self):
        result = self.run_cli('import sys\nsys.exit(124)')
        self.assertEqual(result['caller_exit_code'], 124)
        self.assertFalse(result['timeout'])

    def test_timeout_cleans_child_after_parent_ends(self):
        heartbeat = self.root / 'heartbeat'
        ready = self.root / 'ready.json'; parent_done = self.root / 'parent-ready.json'
        stop = self.root / 'stop'; stopped = self.root / 'stopped'
        child = self.root / 'child.py'
        child.write_text('\n'.join([
            'import os,json,signal,time', 'from pathlib import Path',
            'signal.signal(signal.SIGTERM,signal.SIG_IGN)',
            f'heartbeat=Path({str(heartbeat)!r})', f'stop=Path({str(stop)!r})',
            "heartbeat.write_text('x')",
            f"Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid(),'ready_at':time.time()}}))",
            'while not stop.exists():',
            " with heartbeat.open('a') as f:f.write('x')", ' time.sleep(.02)',
            f"Path({str(stopped)!r}).write_text('stopped')",
        ]))
        code = '\n'.join([
            'import json,subprocess,sys,time', 'from pathlib import Path',
            f'p=subprocess.Popen([sys.executable,{str(child)!r}])', f'ready=Path({str(ready)!r})',
            'while not ready.exists():',
            " if p.poll() is not None:raise RuntimeError('child exited before readiness')",
            ' time.sleep(.01)',
            f"Path({str(parent_done)!r}).write_text(json.dumps({{'child_pid':p.pid,'ready_seen_at':time.time()}}))",
        ])
        complete = False
        try:
            result = self.run_cli(code, timeout=5)
            details = {'capture': result,
                       'stderr': (self.root/'out/stderr.log').read_text(),
                       'events': (self.root/'out/events.jsonl').read_text(),
                       'readiness': {p.name: p.read_text() if p.exists() else None
                                     for p in (ready, parent_done)}}
            message = json.dumps(details, ensure_ascii=False)
            self.assertTrue(ready.exists() and parent_done.exists(), message)
            self.assertTrue(result['timeout'], message)
            self.assertEqual(result['caller_returncode'], 0, message)
            self.assertEqual(result['collector_errors'], [], message)
            self.assertEqual(result['cleanup_errors'], [], message)
            self.assertTrue(heartbeat.exists(), message)
            self.assertGreaterEqual(heartbeat.stat().st_mtime, result['started_at'] + 5, message)
            size = heartbeat.stat().st_size; time.sleep(.1)
            self.assertEqual(heartbeat.stat().st_size, size, message)
            complete = True
        finally:
            stop.touch()
            if not complete and ready.exists():
                deadline = time.monotonic() + 1
                while not stopped.exists() and time.monotonic() < deadline:
                    time.sleep(.02)

    def test_cancel_reaps_owned_group_and_preserves_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(15)'])
        adapter = self.root / 'adapter.py'
        adapter.write_text(f"import sys\nsys.path.insert(0,{str(Path(capture.__file__).parent)!r})\n"
                           "from pathlib import Path\nimport cross_check_capture as c\n"
                           f"c.capture([sys.executable,'-u','-c','import time;time.sleep(15)'],cwd={str(self.root)!r},prompt_file={str(self.prompt)!r},output_dir={str(self.root/'out')!r},timeout=10)\n")
        process = subprocess.Popen([sys.executable, str(adapter)])
        try:
            end=time.monotonic()+3
            while not (self.root/'out/events.jsonl').exists() and time.monotonic()<end:time.sleep(.02)
            time.sleep(.1);process.send_signal(signal.SIGTERM);process.wait(timeout=3)
            result=json.loads((self.root/'out/capture.json').read_text())
            self.assertEqual(result['cancel_signal'], signal.SIGTERM)
            self.assertFalse(result['timeout'])
            self.assertIsNone(unrelated.poll())
        finally:
            if process.poll() is None:process.kill();process.wait()
            unrelated.terminate();unrelated.wait()


if __name__ == '__main__':
    unittest.main()
