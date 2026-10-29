import importlib.util
import json
import sys
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "skills" / "cross-check" / "scripts" / "collect_meta.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/cross-check/scripts'))
SPEC = importlib.util.spec_from_file_location("collect_meta", SCRIPT)
collect_meta = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(collect_meta)

SESSION = "0190aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
BODY = "結論です。" + "根拠を示します。" * 80


class CollectMetaTest(unittest.TestCase):
    def test_sdk_quota_fixture_preserves_exact_failure_metadata_and_raw(self):
        fixture = Path(__file__).parent / 'fixtures/claude-session-quota.jsonl'
        args = self.args('claude', model='claude-fable-5-1', exit_code=1)
        Path(args.raw).write_bytes(fixture.read_bytes())
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta['status']), (1, 'error'))
        failure = meta['claude_error']
        self.assertEqual(failure['error_kind'], 'usage_limit_reached')
        self.assertEqual(failure['error_status'], 429)
        self.assertEqual(failure['error_origin'], 'first_party')
        self.assertTrue(failure['is_api_error_message'])
        self.assertEqual(failure['rate_limit_info']['overageDisabledReason'], 'org_level_disabled')
        self.assertEqual(Path(args.raw).read_bytes(), fixture.read_bytes())

    def test_schema_conflict_is_preserved_in_failure_metadata(self):
        fixture = Path(__file__).parent / 'fixtures/claude-session-quota.jsonl'
        events = [json.loads(line) for line in fixture.read_text().splitlines()]
        events[0]['api_error_kind'] = 'authentication_failed'
        args = self.args('claude', model='claude-fable-5-1', exit_code=1)
        original = '\n'.join(json.dumps(e) for e in events)
        Path(args.raw).write_text(original)
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta['status']), (1, 'error'))
        self.assertFalse(meta['claude_error']['valid'])
        self.assertTrue(meta['claude_error']['schema_errors'])
        self.assertEqual(Path(args.raw).read_text(), original)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = unittest.mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.prompt = self.root / "q.md"
        self.prompt.write_text("質問", encoding="utf-8")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def args(self, side, *, model="", exit_code=0, timed_out=False, out_text="", err_text="", raw=None):
        base = self.root / side
        (base.parent).mkdir(exist_ok=True)
        out, err, rawp, meta = (str(base) + s for s in (".out", ".err", ".raw.json", ".meta.json"))
        Path(out).write_text(out_text, encoding="utf-8")
        Path(err).write_text(err_text, encoding="utf-8")
        if raw is not None:
            Path(rawp).write_text(json.dumps(raw), encoding="utf-8")
        argv = [
            "--side", side, "--model", model, "--effort", "high", "--exit-code", str(exit_code),
            "--timeout", "600", "--started-at", "s", "--ended-at", "e", "--elapsed", "1",
            "--prompt-file", str(self.prompt), "--cwd", str(self.root),
            "--out", out, "--err", err, "--raw", rawp, "--meta", meta,
        ]
        if timed_out:
            argv.append("--timed-out")
        return collect_meta.parse(argv)

    def write_rollout(self, messages, model="gpt-test", effort="high", *, phase_schema=False):
        day = self.home / ".codex" / "sessions" / "2026" / "10" / "26"
        day.mkdir(parents=True, exist_ok=True)
        lines = [{"type": "turn_context", "payload": {"model": model, "effort": effort}}]
        for message in messages:
            channel, text = message if isinstance(message, tuple) else ("final", message)
            payload = {"type": "message", "role": "assistant",
                       "content": [{"type": "output_text", "text": text}]}
            if phase_schema:
                payload["phase"] = "final_answer" if channel == "final" else channel
            else:
                payload["channel"] = channel
            lines.append({"type": "response_item", "payload": payload})
        (day / f"rollout-2026-10-26T00-00-00-{SESSION}.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")

    def test_codex_reads_model_and_effort_from_rollout(self):
        self.write_rollout([BODY])
        args = self.args("codex", model="gpt-test", out_text=BODY, err_text=f"session id: {SESSION}\n")
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta["status"]), (0, "ok"))
        self.assertEqual(meta["model_observed"], "gpt-test")
        self.assertFalse(meta["model_mismatch"])
        self.assertFalse(meta["effort_mismatch"])

    def test_codex_hook_reply_is_replaced_by_last_substantive_message(self):
        reply = "点検しました。修正が必要な箇所はありません。"
        self.write_rollout(["途中経過です。", BODY, reply])
        args = self.args("codex", out_text=reply, err_text=f"session id: {SESSION}\n")
        meta, code = collect_meta.collect(args)
        self.assertEqual(code, 0)
        self.assertEqual(Path(args.out).read_text(encoding="utf-8").strip(), BODY)
        self.assertEqual(Path(args.out + ".hook").read_text(encoding="utf-8"), reply)
        self.assertIn("hook check reply", meta["note"])

    def test_codex_short_answer_without_hook_pattern_is_kept(self):
        self.write_rollout([BODY, "短い答えです。"])
        args = self.args("codex", out_text="短い答えです。", err_text=f"session id: {SESSION}\n")
        collect_meta.collect(args)
        self.assertEqual(Path(args.out).read_text(encoding="utf-8"), "短い答えです。")
        self.assertFalse(Path(args.out + ".hook").exists())

    def test_codex_hook_recovery_ignores_nonfinal_messages(self):
        reply = "点検しました。修正が必要な箇所はありません。"
        for channel in ("analysis", "commentary", None):
            with self.subTest(channel=channel):
                self.write_rollout([BODY, (channel, "途中の検討です。" * 80), reply])
                args = self.args("codex", out_text=reply, err_text=f"session id: {SESSION}\n")
                meta, code = collect_meta.collect(args)
                self.assertEqual((code, meta["status"]), (0, "ok"))
                self.assertEqual(Path(args.out).read_text(encoding="utf-8").strip(), BODY)

    def test_codex_hook_recovery_keeps_short_final_answer(self):
        reply = "点検しました。"
        body = "推奨はAです。理由は費用です。"
        self.write_rollout([BODY, body, reply, reply])
        args = self.args("codex", out_text=reply, err_text=f"session id: {SESSION}\n")
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta["status"]), (0, "ok"))
        self.assertEqual(Path(args.out).read_text(encoding="utf-8").strip(), body)

    def test_codex_hook_recovery_uses_current_cli_phase_schema(self):
        reply = "点検しました。"
        self.write_rollout([BODY, ("commentary", "途中の検討です。" * 80), reply], phase_schema=True)
        args = self.args("codex", out_text=reply, err_text=f"session id: {SESSION}\n")
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta["status"]), (0, "ok"))
        self.assertEqual(Path(args.out).read_text(encoding="utf-8").strip(), BODY)

    def test_codex_conflicting_final_markers_are_not_recovered(self):
        reply = "点検しました。"
        for channel, phase in (("final", "commentary"), ("analysis", "final_answer")):
            with self.subTest(channel=channel, phase=phase):
                self.write_rollout([BODY, reply])
                rollout = Path(collect_meta.codex_rollout(SESSION))
                records = [json.loads(line) for line in rollout.read_text(encoding="utf-8").splitlines()]
                records[1]["payload"].update(channel=channel, phase=phase)
                rollout.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
                args = self.args("codex", out_text=reply, err_text=f"session id: {SESSION}\n")
                meta, code = collect_meta.collect(args)
                self.assertEqual((code, meta["status"]), (6, "output_unverified"))
                self.assertEqual(Path(args.out).read_text(encoding="utf-8"), reply)

    def test_codex_unrecoverable_hook_reply_is_not_success(self):
        reply = "点検しました。"
        for messages in ([('analysis', BODY), reply], [(None, BODY), reply], [reply], [BODY]):
            with self.subTest(messages=messages):
                self.write_rollout(messages)
                args = self.args("codex", out_text=reply, err_text=f"session id: {SESSION}\n")
                meta, code = collect_meta.collect(args)
                self.assertEqual((code, meta["status"]), (6, "output_unverified"))
                self.assertEqual(Path(args.out).read_text(encoding="utf-8"), reply)
                self.assertFalse(Path(args.out + ".hook").exists())

    def test_codex_hook_reply_without_rollout_is_not_success(self):
        args = self.args("codex", out_text="点検しました。", err_text="model: gpt-test\n")
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta["status"]), (6, "output_unverified"))

    def test_codex_normal_answer_containing_hook_phrase_is_kept(self):
        body = "点検しました。推奨はAです。理由は費用です。"
        self.write_rollout([BODY, body])
        args = self.args("codex", out_text=body, err_text=f"session id: {SESSION}\n")
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta["status"]), (0, "ok"))
        self.assertEqual(Path(args.out).read_text(encoding="utf-8"), body)

    def test_unrecoverable_hook_preserves_cli_failure_and_timeout(self):
        for exit_code, timed_out, expected in ((9, False, (9, "error")), (143, True, (124, "timeout"))):
            with self.subTest(exit_code=exit_code):
                args = self.args("codex", out_text="点検しました。", err_text="model: gpt-test\n",
                                 exit_code=exit_code, timed_out=timed_out)
                meta, code = collect_meta.collect(args)
                self.assertEqual((code, meta["status"]), expected)

        args = self.args("codex", out_text="点検しました。", exit_code=1,
                         err_text="model: gpt-test\nunrecognized_model\n")
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta["status"]), (5, "model_mismatch"))
        meta, code = collect_meta.collect(self.args("codex", out_text="点検しました。"))
        self.assertEqual((code, meta["status"]), (4, "model_unverified"))

    def test_model_omitted_records_observed_without_mismatch(self):
        self.write_rollout([BODY], model="cli-default")
        meta, code = collect_meta.collect(self.args("codex", out_text=BODY, err_text=f"session id: {SESSION}\n"))
        self.assertEqual(code, 0)
        self.assertIsNone(meta["model_requested"])
        self.assertIsNone(meta["model_mismatch"])
        self.assertEqual(meta["model_observed"], "cli-default")

    def test_requested_model_replaced_by_cli_is_mismatch(self):
        self.write_rollout([BODY], model="other-model")
        meta, code = collect_meta.collect(
            self.args("codex", model="gpt-test", out_text=BODY, err_text=f"session id: {SESSION}\n"))
        self.assertEqual((code, meta["status"]), (5, "model_mismatch"))

    def test_codex_model_id_requires_exact_match(self):
        for source in ("rollout", "header"):
            for requested, observed in (("gpt-4.1", "gpt-4.1"),
                                        ("gpt-4.1", "gpt-4.1-mini"),
                                        ("gpt-4.1", "gpt-4.1-2025-04-14"),
                                        ("gpt-4.1", "other-gpt-4.1"),
                                        ("gpt-4.1", "GPT-4.1"),
                                        ("gpt-6.1-sol", "gpt-6.1-sol")):
                with self.subTest(source=source, requested=requested, observed=observed):
                    if source == "rollout":
                        self.write_rollout([BODY], model=observed)
                        err = f"session id: {SESSION}\n"
                    else:
                        err = f"model: {observed}\n"
                    meta, code = collect_meta.collect(
                        self.args("codex", model=requested, out_text=BODY, err_text=err))
                    mismatch = requested != observed
                    self.assertEqual((code, meta["status"]), (5, "model_mismatch") if mismatch else (0, "ok"))
                    self.assertEqual(meta["model_mismatch"], mismatch)
                    self.assertEqual(meta["model_observed"], observed)

    def test_claude_model_id_matches_exact_usage_entry(self):
        cases = (
            ("claude-test", ["claude-test"], False),
            ("claude-test", ["claude-test-mini"], True),
            ("claude-test", ["other-claude-test"], True),
            ("claude-test", ["CLAUDE-test"], True),
            ("claude-test", ["claude-test", "helper-model"], False),
            ("claude-test", ["claude-test-mini", "helper-model"], True),
            ("claude-test", ["helper-model"], True),
            ("", ["claude-haiku-4-5-20251001", "claude-opus-4-8[1m]"], None),
            ("claude-opus-4-8", ["claude-opus-4-8[1m]"], True),
            ("claude-opus-4-8[1m]", ["claude-haiku-4-5-20251001", "claude-opus-4-8[1m]"], False),
        )
        for requested, observed, mismatch in cases:
            with self.subTest(requested=requested, observed=observed):
                raw = {"result": BODY, "modelUsage": dict.fromkeys(observed, {})}
                meta, code = collect_meta.collect(self.args("claude", model=requested, raw=raw))
                self.assertEqual((code, meta["status"]), (5, "model_mismatch") if mismatch else (0, "ok"))
                self.assertEqual(meta["model_mismatch"], mismatch)
                self.assertEqual(meta["model_observed"], ",".join(sorted(observed)))

    def test_claude_result_and_model_usage(self):
        raw = {"session_id": "s1", "result": BODY, "modelUsage": {"claude-test": {}}, "is_error": False}
        meta, code = collect_meta.collect(self.args("claude", model="claude-test", raw=raw))
        self.assertEqual((code, meta["status"]), (0, "ok"))
        self.assertEqual(meta["model_observed"], "claude-test")

    def test_claude_is_error_becomes_error_status(self):
        raw = {"session_id": "s1", "result": "", "modelUsage": {}, "is_error": True, "subtype": "api_error"}
        meta, code = collect_meta.collect(self.args("claude", raw=raw))
        self.assertEqual((code, meta["status"]), (1, "error"))
        self.assertIn("api_error", meta["note"])

    def test_claude_hook_reply_uses_only_same_session_attested_stderr_result(self):
        hook = "点検しました。"
        raw = {"type": "result", "session_id": "s1", "result": hook, "modelUsage": {"claude-test": {}}}
        actual = {"type": "result", "session_id": "s1", "result": BODY, "modelUsage": {"claude-test": {}}}
        for stderr, expected in ((json.dumps(actual), BODY), (BODY, hook),
                                 (json.dumps(dict(actual, session_id="other")), hook),
                                 (json.dumps(dict(actual, modelUsage={"other-model": {}})), hook),
                                 (json.dumps({"type":"assistant", "message":"diagnostic"}) + "\n" + json.dumps(actual), hook),
                                 (json.dumps(dict(actual, session_id="other")) + "\n" +
                                  json.dumps({"type": "system", "session_id": "s1"}), hook)):
            with self.subTest(stderr=stderr):
                args = self.args("claude", model="claude-test", raw=raw, err_text=stderr)
                meta, code = collect_meta.collect(args)
                self.assertEqual(Path(args.out).read_text(), expected)
                self.assertEqual(code, 0 if expected == BODY else 6)
                self.assertEqual(json.loads(Path(args.raw).read_text()), raw)

    def test_successful_stderr_does_not_clear_stdout_terminal_failure(self):
        raw = {'type': 'result', 'session_id': 's1', 'result': '점검했습니다.',
               'modelUsage': {'claude-test': {}}, 'is_error': True,
               'api_error': 'authentication_failed'}
        success = {'type': 'result', 'session_id': 's1', 'result': BODY,
                   'modelUsage': {'claude-test': {}}, 'is_error': False}
        args = self.args('claude', model='claude-test', raw=raw, err_text=json.dumps(success))
        meta, code = collect_meta.collect(args)
        self.assertEqual((code, meta['status']), (1, 'error'))
        self.assertTrue(meta['claude_error']['is_error'])
        self.assertEqual(meta['claude_error']['error_kind'], 'authentication_failed')
        self.assertEqual(Path(args.out).read_text(), raw['result'])
        self.assertEqual(json.loads(Path(args.raw).read_text()), raw)

    def test_timeout_and_empty_output_codes(self):
        meta, code = collect_meta.collect(self.args("codex", timed_out=True, exit_code=143))
        self.assertEqual((code, meta["status"]), (124, "timeout"))
        meta, code = collect_meta.collect(self.args("codex"))
        self.assertEqual((code, meta["status"]), (3, "empty_output"))

    def test_unverified_model_code(self):
        meta, code = collect_meta.collect(self.args("codex", out_text=BODY))
        self.assertEqual((code, meta["status"]), (4, "model_unverified"))


if __name__ == "__main__":
    unittest.main()
