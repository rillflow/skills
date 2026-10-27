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
        heartbeat = self.root / 'heartbeat';child = self.root / 'child.py'
        child.write_text("import signal,time\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                         f"p=Path({str(heartbeat)!r})\nwhile True:\n with p.open('a') as f:f.write('x')\n time.sleep(.02)\n")
        code = f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,{str(child)!r}])\ntime.sleep(.15)\n"
        result = self.run_cli(code, timeout=.3)
        self.assertTrue(result['timeout'])
        size = heartbeat.stat().st_size;time.sleep(.1)
        self.assertEqual(heartbeat.stat().st_size, size)

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
