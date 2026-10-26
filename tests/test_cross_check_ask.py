import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "skills" / "cross-check" / "scripts" / "ask.sh"
OPTIONS = ("--prompt-file", "--out-dir", "--cwd", "--timeout", "--model", "--effort")


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

    def run_ask(self, side, *args):
        return subprocess.run(
            ["bash", str(SCRIPT), side, *args], cwd=self.root, env=self.env,
            capture_output=True, text=True, timeout=5,
        )

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


if __name__ == "__main__":
    unittest.main()
