"""QAのhelper許可を、同じ実体のインストール経路と固定fixtureへ限定する。"""
from __future__ import annotations

import hashlib
from pathlib import Path


def helper_permissions(canonical, installed, fixture, *, timeout=120):
    canonical = Path(canonical).absolute()
    fixture = Path(fixture).absolute()
    digest = hashlib.sha256(canonical.read_bytes()).hexdigest()
    paths = list(dict.fromkeys([canonical, *(Path(p).absolute() for p in installed)]))
    verified = []
    rules = []
    prompts = (fixture / 'peer-prompt.txt', fixture / 'results/codex-prompt.md')
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.resolve() != canonical.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'helper identity differs: {path}')
        verified.append({'path': str(path), 'realpath': str(path.resolve()), 'sha256': digest})
        for executable in (str(path), 'bash ' + str(path)):
            for prompt in prompts:
                for cwd_option in (' --cwd ' + str(fixture), ''):
                    command = (f'{executable} codex --prompt-file {prompt} --out-dir {fixture / "peer"}'
                               f'{cwd_option} --timeout {timeout} --model gpt-6-astra --effort high')
                    rules.append('Bash(' + command + ')')
    return {'rules': rules, 'verified_helpers': verified, 'fixture': str(fixture),
            'prompt_paths': [str(p) for p in prompts], 'peer_output': str(fixture / 'peer')}


def permits_helper_command(command, rules):
    """公式のcompound分解に対応する、生成した完全一致規則の合成検査。"""
    # echoは終了コード表示だけに固定する。任意のcompoundを許可しない。
    suffix = '; echo "EXIT=$?"'
    if command.endswith(suffix):
        command = command[:-len(suffix)]
    return 'Bash(' + command + ')' in rules
