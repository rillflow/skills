import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).parents[1] / "skills" / "cross-check" / "scripts" / "ask.sh"
OPTIONS = ("--prompt-file", "--out-dir", "--cwd", "--timeout", "--model", "--effort")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/cross-check/scripts'))
SPEC = importlib.util.spec_from_file_location("run_cli", SCRIPT.parent / "run_cli.py")
run_cli = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(run_cli)


class AskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.marker = self.root / "cli-started"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.prompt = self.root / "prompt"
        self.prompt.write_text("質問", encoding="utf-8")
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        self.env.pop("CDPATH", None)
        for name in ("codex", "claude"):
            cli = self.bin / name
            cli.write_text(
                "#!/bin/sh\n"
                f"touch '{self.marker}'\n"
                "cat >/dev/null\n"
                + ("echo 'model: synthetic-model' >&2\necho '応答です。'\n" if name == "codex" else
                   "echo '{\"result\":\"応答です。\",\"modelUsage\":{\"synthetic-model\":{}}}'\n"),
                encoding="utf-8",
            )
            cli.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_ask(self, side, *args, timeout=10):
        if side == "claude" and "--model" not in args:
            args += ("--model", "synthetic-model")
        return subprocess.run(
            ["bash", str(SCRIPT), side, *args], cwd=self.root, env=self.env,
            capture_output=True, text=True, timeout=timeout,
        )

    def meta(self, result):
        return json.loads((self.root / result.stdout.strip()).read_text(encoding="utf-8"))

    def python_cli(self, side, code):
        cli = self.bin / side
        cli.write_text(f"#!{sys.executable}\n" + code, encoding="utf-8")
        cli.chmod(0o755)

    def wait_for_exit(self, pid):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.02)
        return False

    def test_missing_option_values_exit_without_starting_cli(self):
        valid = ["--prompt-file", str(self.prompt), "--out-dir", str(self.root / "output"),
                 "--cwd", str(self.root)]
        for option in OPTIONS:
            for suffix in ([], [""], ["--out-dir", str(self.root / "output")]):
                with self.subTest(option=option, suffix=suffix):
                    result = self.run_ask("codex", *valid, option, *suffix)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertFalse(self.marker.exists())
                    self.assertFalse((self.root / "output").exists())

    def test_valid_arguments_reach_cli_and_collect_output(self):
        for side in ("codex", "claude"):
            with self.subTest(side=side):
                outdir = self.root / side
                result = self.run_ask(
                    side, "--prompt-file", str(self.prompt), "--out-dir", str(outdir),
                    "--cwd", str(self.root), "--timeout", "5", "--model", "synthetic-model",
                    "--effort", "high",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(self.marker.exists())
                meta = json.loads(Path(result.stdout.strip()).read_text(encoding="utf-8"))
                self.assertEqual(meta["status"], "ok")
                self.assertEqual(meta["model_observed"], "synthetic-model")
                self.assertEqual(Path(meta["out"]).read_text(encoding="utf-8").strip(), "応答です。")

    def test_relative_cwd_and_paths_are_resolved_from_caller(self):
        work = self.root / "work area"
        work.mkdir()
        (self.root / "work").symlink_to(work, target_is_directory=True)
        for side in ("codex", "claude"):
            with self.subTest(side=side):
                self.python_cli(side, "import json, os, sys\n"
                    "if '-C' in sys.argv: os.chdir(sys.argv[sys.argv.index('-C') + 1])\n"
                    "body = json.dumps({'cwd': os.getcwd(), 'prompt': sys.stdin.read()})\n"
                    + ("print('model: synthetic-model', file=sys.stderr)\nprint(body)\n" if side == "codex" else
                       "print(json.dumps({'result': body, 'modelUsage': {'synthetic-model': {}}}))\n"))
                result = self.run_ask(side, "--prompt-file", "prompt", "--out-dir", f"relative-{side}",
                                      "--cwd", "work", "--timeout", "5")
                self.assertEqual(result.returncode, 0, result.stderr)
                meta = self.meta(result)
                body = json.loads((self.root / meta["out"]).read_text(encoding="utf-8"))
                self.assertEqual(body, {"cwd": str(work.resolve()), "prompt": "質問"})
                self.assertEqual(meta["cwd"], str(work.resolve()))
                self.assertEqual(meta["prompt_file"], str(self.prompt.resolve()))
                self.assertEqual(meta["status"], "ok")

    def test_cdpath_does_not_change_caller_relative_cwd(self):
        work = self.root / "work area"
        work.mkdir()
        search = self.root / "search path"
        search.mkdir()
        for name in ("work", "-work"):
            (self.root / name).symlink_to(work, target_is_directory=True)
            (search / name).mkdir()
        for side in ("codex", "claude"):
            self.python_cli(side, "import json,os,sys\n"
                "if '-C' in sys.argv: os.chdir(sys.argv[sys.argv.index('-C')+1])\n"
                "body=json.dumps(dict(cwd=os.getcwd(),prompt=sys.stdin.read(),cdpath=os.environ.get('CDPATH')))\n"
                + ("print('model: synthetic-model',file=sys.stderr)\nprint(body)\n" if side == "codex" else
                   "print(json.dumps(dict(result=body,modelUsage={'synthetic-model':{}})))\n"))
            for current_first in (False, True):
                cdpath = (".:" if current_first else "") + str(search)
                self.env["CDPATH"] = cdpath
                for name in ("work", "-work"):
                    with self.subTest(side=side, current_first=current_first, name=name):
                        outdir = f"cdpath-{side}-{current_first}-{name}"
                        result = self.run_ask(side, "--prompt-file", "prompt", "--out-dir", outdir,
                                              "--cwd", name, "--timeout", "5")
                        self.assertEqual(result.returncode, 0, result.stderr)
                        meta = self.meta(result)
                        body = json.loads((self.root / meta["out"]).read_text(encoding="utf-8"))
                        self.assertEqual(body, dict(cwd=str(work.resolve()), prompt="質問", cdpath=cdpath))
                        self.assertEqual(meta["cwd"], str(work.resolve()))
                        self.assertEqual(meta["prompt_file"], str(self.prompt.resolve()))
                        self.assertEqual(meta["status"], "ok")
                        self.assertEqual(Path(meta["out"]).parent, Path(outdir))
                        self.assertNotIn("\n", meta["cwd"])

    def test_cli_failure_and_cli_exit_124_are_not_timeouts(self):
        for code in (7, 124, "SIGTERM"):
            with self.subTest(code=code):
                self.python_cli("codex", "import os, signal, sys\nprint('model: synthetic-model', file=sys.stderr, flush=True)\n"
                                "print('synthetic CLI failure', file=sys.stderr, flush=True)\n"
                                + ("os.kill(os.getpid(), signal.SIGTERM)\n" if code == "SIGTERM" else f"sys.exit({code})\n"))
                result = self.run_ask("codex", "--prompt-file", str(self.prompt), "--out-dir", str(self.root / str(code)), "--timeout", "2")
                meta = self.meta(result)
                self.assertEqual((result.returncode, meta["status"], meta["timeout"]),
                                 (143 if code == "SIGTERM" else code, "error", False))
                self.assertIn("synthetic CLI failure", Path(meta["err"]).read_text())

    def test_timeout_kills_reparented_term_ignoring_child_only_in_its_group(self):
        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.addCleanup(sentinel.wait)
        self.addCleanup(sentinel.kill)
        ticker = ("import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                  "f=open(sys.argv[1], 'a', buffering=1); end=time.monotonic()+30\n"
                  "while time.monotonic()<end: f.write('beat\\n'); time.sleep(0.05)\n")
        for parent_ignores_term in (False, True):
            with self.subTest(parent_ignores_term=parent_ignores_term):
                heartbeat = self.root / f"heartbeat-{parent_ignores_term}"
                pidfile = self.root / f"child-{parent_ignores_term}"
                self.python_cli("codex", "import signal, subprocess, sys, time\nfrom pathlib import Path\n"
                    + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if parent_ignores_term else "")
                    + f"p=subprocess.Popen([sys.executable, '-c', {ticker!r}, {str(heartbeat)!r}], "
                      "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                    + f"Path({str(pidfile)!r}).write_text(str(p.pid))\n"
                      "while not Path(" + repr(str(heartbeat)) + ").exists():\n time.sleep(.01)\n"
                      "print('fixture child ready', file=sys.stderr, flush=True)\n"
                      "print('model: synthetic-model', file=sys.stderr, flush=True)\ntime.sleep(30)\n")
                started = time.monotonic()
                result = self.run_ask("codex", "--prompt-file", str(self.prompt), "--out-dir", str(self.root / str(parent_ignores_term)),
                                      "--timeout", "3", timeout=15)
                meta = self.meta(result)
                self.assertEqual((result.returncode, meta["status"], meta["timeout"]), (124, "timeout", True))
                diagnostics = {"meta": meta, "stderr": Path(meta["err"]).read_text(),
                               "ready": heartbeat.exists(), "pid_record": pidfile.read_text() if pidfile.exists() else None}
                if not heartbeat.exists() or "fixture child ready" not in diagnostics["stderr"]:
                    Path("/private/tmp/cross-check-descendant-readiness-failure.json").write_text(json.dumps(diagnostics))
                self.assertTrue(heartbeat.exists(), json.dumps(diagnostics, ensure_ascii=False))
                self.assertIn("fixture child ready", diagnostics["stderr"])
                before = heartbeat.stat().st_size
                time.sleep(0.2)
                self.assertEqual(heartbeat.stat().st_size, before)
                self.assertTrue(self.wait_for_exit(int(pidfile.read_text())))
                self.assertLess(time.monotonic() - started, 20)
                self.assertIsNone(sentinel.poll())

    def test_cli_launch_errors_are_reported(self):
        proxy = self.bin / "python3"
        proxy.write_text(f"#!{sys.executable}\nimport os,sys\nos.execv({sys.executable!r}, [{sys.executable!r}] + sys.argv[1:])\n")
        proxy.chmod(0o755)
        self.env["PATH"] = str(self.bin) + os.pathsep + "/usr/bin:/bin"
        for missing in (True, False):
            with self.subTest(missing=missing):
                cli = self.bin / "codex"
                if missing:
                    cli.unlink()
                else:
                    cli.write_text("#!/bin/sh\nexit 0\n")
                    cli.chmod(0o644)
                result = self.run_ask("codex", "--prompt-file", str(self.prompt), "--out-dir", str(self.root / str(missing)))
                meta = self.meta(result)
                self.assertEqual((result.returncode, meta["status"]), (127 if missing else 126, "error"))
                self.assertIn("launch_error", meta["note"])

    def test_invalid_runner_result_cannot_be_success(self):
        for text, code in (("not JSON", 0), ("", 2), ('{"exit_code": 124, "timeout": false}', 0)):
            with self.subTest(text=text, code=code):
                proxy = self.bin / "python3"
                proxy.write_text(f"#!{sys.executable}\nimport os,sys\nfrom pathlib import Path\n"
                    "if Path(sys.argv[1]).name == 'run_cli.py':\n"
                    f"    print({text!r}); sys.exit({code})\n"
                    f"os.execv({sys.executable!r}, [{sys.executable!r}] + sys.argv[1:])\n")
                proxy.chmod(0o755)
                result = self.run_ask("codex", "--prompt-file", str(self.prompt), "--out-dir", str(self.root / "protocol"))
                meta = self.meta(result)
                self.assertEqual((result.returncode, meta["status"]), (code or 1, "error"))
                self.assertIn("runner result", meta["note"])
                self.assertEqual(meta["runner_result_raw"], text)
                self.assertIsNone(meta["timeout"])
                self.assertEqual(meta["runner_state"], "invalid")
                self.assertIsNone(meta["cleanup_complete"])
                self.assertFalse(self.marker.exists())

    def test_external_cancellation_cleans_isolated_group(self):
        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"], start_new_session=True)
        self.addCleanup(sentinel.wait)
        self.addCleanup(sentinel.kill)
        ticker = ("import signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                  "signal.signal(signal.SIGINT,signal.SIG_IGN); f=open(sys.argv[1],'a',buffering=1); end=time.monotonic()+90\n"
                  "while time.monotonic()<end: f.write('beat\\n'); time.sleep(.03)\n")
        for whole_group in (True, False):
            for sig in (signal.SIGTERM, signal.SIGINT):
                with self.subTest(whole_group=whole_group, sig=sig):
                    heartbeat = self.root / f"cancel-heartbeat-{whole_group}-{sig}"
                    pidfile = self.root / f"cancel-pids-{whole_group}-{sig}"
                    self.python_cli("codex", "import json,os,signal,subprocess,sys,time\nfrom pathlib import Path\n"
                        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\nsignal.signal(signal.SIGINT,signal.SIG_IGN)\n"
                        f"p=subprocess.Popen([sys.executable,'-c',{ticker!r},{str(heartbeat)!r}], "
                        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                        f"Path({str(pidfile)!r}).write_text(json.dumps(dict(cli=os.getpid(),runner=os.getppid(),child=p.pid)))\n"
                        "print('model: synthetic-model',file=sys.stderr,flush=True)\ntime.sleep(90)\n")
                    started = time.monotonic()
                    wrapper = subprocess.Popen(
                        ["bash", str(SCRIPT), "codex", "--prompt-file", str(self.prompt),
                         "--out-dir", str(self.root / f"cancel-{whole_group}-{sig}"), "--timeout", "20"],
                        cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, start_new_session=True,
                    )
                    pids = {}
                    try:
                        self.assertGreater(wrapper.pid, 0)
                        self.assertNotEqual(wrapper.pid, os.getpgrp())
                        self.assertEqual(os.getpgid(wrapper.pid), wrapper.pid)
                        end = time.monotonic() + 15
                        while (not heartbeat.exists() or not pidfile.exists()) and time.monotonic() < end:
                            time.sleep(.01)
                        ready = heartbeat.exists() and pidfile.exists()
                        if not ready:
                            diagnostics = {"heartbeat_ready": heartbeat.exists(), "pid_ready": pidfile.exists(),
                                           "wrapper_poll": wrapper.poll(), "output_files": {
                                               p.name: p.read_text(errors="replace") for p in
                                               (self.root / f"cancel-{whole_group}-{sig}").glob("*")}}
                            Path(f"/private/tmp/cross-check-cancel-readiness-{whole_group}-{sig}.json").write_text(json.dumps(diagnostics))
                        self.assertTrue(ready, "fixture not ready; see /private/tmp/cross-check-cancel-readiness-*.json")
                        pids = json.loads(pidfile.read_text())
                        if whole_group:
                            os.killpg(wrapper.pid, sig)
                            try:
                                wrapper.wait(timeout=.1)
                            except subprocess.TimeoutExpired:
                                os.killpg(wrapper.pid, signal.SIGKILL)
                        else:
                            os.kill(pids["runner"], sig)
                        out, err = wrapper.communicate(timeout=8)
                        if not whole_group:
                            meta = json.loads(Path(out.strip()).read_text())
                            self.assertEqual((wrapper.returncode, meta["status"], meta["timeout"]),
                                             (128 + sig, "error", False), err)
                            self.assertIn(f"cancelled by signal {sig}", meta["note"])
                            self.assertEqual(json.loads(meta["runner_result_raw"])["cancel_signal"], sig)
                        before = heartbeat.stat().st_size
                        time.sleep(.15)
                        self.assertEqual(heartbeat.stat().st_size, before)
                        self.assertTrue(self.wait_for_exit(pids["cli"]))
                        self.assertTrue(self.wait_for_exit(pids["child"]))
                        self.assertLess(time.monotonic() - started, 30)
                        self.assertIsNone(sentinel.poll())
                    finally:
                        if wrapper.poll() is None:
                            try:
                                os.killpg(wrapper.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            try:
                                wrapper.communicate(timeout=.2)
                            except subprocess.TimeoutExpired:
                                os.killpg(wrapper.pid, signal.SIGKILL)
                                wrapper.communicate()

    def test_cancellation_during_startup_keeps_first_signal_without_reentry(self):
        for first in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(first=first):
                handlers = {}
                process = SimpleNamespace(pid=424242, returncode=None)

                def register(sig, handler):
                    old = handlers.get(sig, signal.SIG_DFL)
                    handlers[sig] = handler
                    return old

                def spawn(*_args, **_kwargs):
                    handlers[first](first, None)
                    return process

                def kill(pid, sig):
                    if sig == 0:
                        raise ProcessLookupError()
                    self.assertEqual((pid, sig), (424242, signal.SIGKILL))
                    second = signal.SIGINT if first == signal.SIGTERM else signal.SIGTERM
                    handlers[second](second, None)

                def poll():
                    process.returncode = -signal.SIGKILL
                    return process.returncode

                process.poll = poll
                process.wait = lambda timeout: poll()
                args = SimpleNamespace(command=["synthetic"], cwd=str(self.root), timeout=20,
                                       stdin=str(self.prompt), stdout=str(self.root / "out"), stderr=str(self.root / "err"))
                with unittest.mock.patch.object(run_cli.signal, "signal", side_effect=register), \
                     unittest.mock.patch.object(run_cli.signal, "pthread_sigmask", return_value=set()), \
                     unittest.mock.patch.object(run_cli.subprocess, "Popen", side_effect=spawn), \
                     unittest.mock.patch.object(run_cli.os, "killpg", side_effect=kill) as killed:
                    result = run_cli.run(args)
                self.assertEqual((result["exit_code"], result["timeout"], result["cancel_signal"]),
                                 (128 + first, False, first))
                self.assertEqual(sum(call.args[1] != 0 for call in killed.call_args_list), 1)

    def test_cancellation_after_reaping_does_not_signal_reused_group(self):
        handlers = {}
        process = SimpleNamespace(pid=424242, returncode=None)
        blocked = False
        delivered = False

        def register(sig, handler):
            old = handlers.get(sig, signal.SIG_DFL)
            handlers[sig] = handler
            return old

        def poll():
            self.assertTrue(blocked)
            process.returncode = 0
            return 0

        def mask(how, _signals):
            nonlocal blocked, delivered
            blocked = how == signal.SIG_BLOCK
            if not blocked and process.returncode is not None and not delivered:
                delivered = True
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            return set()

        process.poll = poll
        args = SimpleNamespace(command=["synthetic"], cwd=str(self.root), timeout=20,
                               stdin=str(self.prompt), stdout=str(self.root / "out"), stderr=str(self.root / "err"))
        with unittest.mock.patch.object(run_cli.signal, "signal", side_effect=register), \
             unittest.mock.patch.object(run_cli.signal, "pthread_sigmask", side_effect=mask), \
             unittest.mock.patch.object(run_cli.subprocess, "Popen", return_value=process), \
             unittest.mock.patch.object(run_cli.os, "killpg") as killed:
            result = run_cli.run(args)
        self.assertEqual((result["exit_code"], result["timeout"]), (143, False))
        self.assertFalse(any(call.args[1] != 0 for call in killed.call_args_list))


if __name__ == "__main__":
    unittest.main()
