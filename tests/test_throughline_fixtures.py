"""モデルの判断ではなく、合成試験環境の隔離と再現性を検証する。"""
import contextlib
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("throughline_fixtures", ROOT / "docs/qa/throughline/generate_fixtures.py")
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = ROOT / "skills/throughline"

    def test_only_skill_path_changes_and_source_remains_identical(self):
        before = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        result = fixtures.generate(self.root / "generated", self.source)
        for case in fixtures.CASES:
            copy = self.root / "generated" / case / "skill"
            for relative, data in before.items():
                actual = (copy / relative).read_bytes()
                if relative.as_posix() == "SKILL.md":
                    actual = actual.replace(fixtures.SYNTHETIC_HOME_POLICY.encode(), fixtures.HOME_POLICY.encode())
                self.assertEqual(actual, data)
            self.assertEqual(set(result["cases"][case]["skill_hashes"]), {p.as_posix() for p in before})
        self.assertEqual(before, {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()})

    def test_policy_states_and_overlapping_locations(self):
        fixtures.generate(self.root / "generated", self.source)
        base = self.root / "generated"
        self.assertFalse((base / "missing/.throughline/policy.md").exists())
        self.assertEqual((base / "empty/.throughline/policy.md").read_bytes(), b"")
        self.assertTrue((base / "directory/.throughline/policy.md").is_dir())
        overlap = (base / "overlap/.throughline/policy.md").read_text()
        self.assertEqual(overlap.count("`./ledgers/*/case.md`"), 2)
        for name in fixtures.CASES:
            root = base / name
            self.assertTrue((root / "environment.synthetic.marker").is_file())
            self.assertTrue((root / "record.synthetic.md").is_file())
            self.assertTrue((root / "synthetic-home/.agents/throughline/policy.md").is_file())

    def test_generation_is_deterministic(self):
        a = fixtures.generate(self.root / "a", self.source)
        b = fixtures.generate(self.root / "b", self.source)
        self.assertEqual(a, b)
        files_a = {p.relative_to(self.root / "a"): p.read_bytes() for p in (self.root / "a").rglob("*") if p.is_file()}
        files_b = {p.relative_to(self.root / "b"): p.read_bytes() for p in (self.root / "b").rglob("*") if p.is_file()}
        self.assertEqual(files_a, files_b)

    def test_existing_destination_is_never_overwritten(self):
        for link in (False, True):
            destination = self.root / ("link" if link else "existing")
            if link:
                destination.symlink_to(self.root / "absent")
            else:
                destination.mkdir()
                (destination / "keep").write_text("preserve")
            with self.assertRaises(FileExistsError):
                fixtures.generate(destination, self.source)
        self.assertEqual((self.root / "existing/keep").read_text(), "preserve")
        self.assertFalse((self.root / "absent").exists())

    def test_ambiguous_source_does_not_create_destination(self):
        source = self.root / "source"
        source.mkdir()
        (source / "SKILL.md").write_text("no home setting")
        with self.assertRaises(ValueError):
            fixtures.generate(self.root / "generated", source)
        self.assertFalse((self.root / "generated").exists())

    def test_destination_within_source_is_rejected_without_changes(self):
        source = self.root / "source"
        shutil.copytree(self.source, source)
        (source / "empty-directory").mkdir()
        alias = self.root / "source-alias"
        alias.symlink_to(source, target_is_directory=True)
        working = self.root / "working"
        working.mkdir()

        def snapshot():
            return {p.relative_to(source): p.read_bytes() if p.is_file() else None
                    for p in source.rglob("*")}

        before = snapshot()
        destinations = (
            source / "generated",
            source / "new-parent/generated",
            source / ".." / source.name / "generated",
            alias / "generated",
            Path("../source/generated"),
        )
        with contextlib.ExitStack() as stack:
            stack.callback(os.chdir, Path.cwd())
            os.chdir(working)
            for destination in destinations:
                with self.subTest(destination=destination):
                    with self.assertRaises(ValueError):
                        fixtures.generate(destination, source)
                    self.assertFalse(destination.exists())
                    self.assertEqual(snapshot(), before)
            with self.assertRaises(ValueError):
                fixtures.generate(Path("../source/generated"), Path("../source"))
            self.assertEqual(snapshot(), before)

        for destination in (source, source / ".." / source.name, alias):
            with self.subTest(destination=destination):
                with self.assertRaises(FileExistsError):
                    fixtures.generate(destination, source)
                self.assertEqual(snapshot(), before)

    def test_source_symlinks_are_still_rejected_before_generation(self):
        source = self.root / "source"
        shutil.copytree(self.source, source)
        alias = self.root / "source-alias"
        alias.symlink_to(source, target_is_directory=True)
        destination = self.root / "generated"
        with self.assertRaises(ValueError):
            fixtures.generate(destination, alias)
        self.assertFalse(destination.exists())
        (source / "input-link").symlink_to(source / "SKILL.md")
        with self.assertRaises(ValueError):
            fixtures.generate(destination, source)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
