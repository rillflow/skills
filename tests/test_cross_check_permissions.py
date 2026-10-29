import tempfile
import unittest
from pathlib import Path

from cross_check_permissions import helper_permissions, permits_helper_command


class HelperPermissionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.canonical = self.root / 'repo/skills/cross-check/scripts/ask.sh'
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_text('#!/bin/bash\nexit 0\n')
        self.aliases = []
        for directory in ('.agents', '.claude', '.codex'):
            path = self.root / directory / 'skills/cross-check/scripts/ask.sh'
            path.parent.mkdir(parents=True)
            path.symlink_to(self.canonical)
            self.aliases.append(path)
        self.fixture = self.root / 'fixture'
        self.fixture.mkdir()
        self.policy = helper_permissions(self.canonical, self.aliases, self.fixture)

    def command(self, helper, *, wrapper='', fixture=None, prompt='peer-prompt.txt'):
        f = fixture or self.fixture
        return (f'{wrapper}{helper} codex --prompt-file {f / prompt} --out-dir {f / "peer"}'
                f' --cwd {f} --timeout 120 --model gpt-6-astra --effort high')

    def test_every_installation_surface_and_direct_bash_form(self):
        for helper in [self.canonical, *self.aliases]:
            for wrapper in ('', 'bash '):
                for prompt in ('peer-prompt.txt', 'results/codex-prompt.md'):
                    command = self.command(helper, wrapper=wrapper, prompt=prompt)
                    self.assertTrue(permits_helper_command(command, self.policy['rules']))
                    self.assertTrue(permits_helper_command(command + '; echo "EXIT=$?"', self.policy['rules']))
        self.assertEqual(len(self.policy['verified_helpers']), 4)
        self.assertEqual(len({v['realpath'] for v in self.policy['verified_helpers']}), 1)

    def test_other_repo_command_fixture_and_output_are_not_approved(self):
        command = self.command(self.aliases[1])
        for denied in (self.command(self.root / 'other/ask.sh'),
                       command.replace(' codex ', ' claude '),
                       self.command(self.aliases[1], fixture=self.root / 'other-fixture'),
                       command.replace(str(self.fixture / 'peer'), str(self.root / 'other-output')),
                       command.replace('peer-prompt.txt', '../outside.txt'),
                       command + '; touch /tmp/unrelated', command + ' --model other-model'):
            self.assertFalse(permits_helper_command(denied, self.policy['rules']), denied)

    def test_same_bytes_at_different_realpath_are_rejected(self):
        copy = self.root / 'different-repo/ask.sh'
        copy.parent.mkdir()
        copy.write_bytes(self.canonical.read_bytes())
        with self.assertRaises(ValueError):
            helper_permissions(self.canonical, [copy], self.fixture)

    def test_missing_installation_surface_is_not_silently_skipped(self):
        with self.assertRaises(FileNotFoundError):
            helper_permissions(self.canonical, [self.root / 'missing/ask.sh'], self.fixture)


if __name__ == '__main__':
    unittest.main()
