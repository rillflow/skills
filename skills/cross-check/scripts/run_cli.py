#!/usr/bin/env python3
"""CLIを専用プロセスグループで実行し、終了状態だけをJSONで返す。"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time

CANCEL_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def kill_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def stop_group(process: subprocess.Popen) -> None:
    # 親の終了後も同じグループを対象にし、KILLの送信前にはリーダーを回収しない。
    for sig in (signal.SIGTERM, signal.SIGKILL):
        kill_group(process.pid, sig)
        if sig == signal.SIGTERM:
            time.sleep(2)


def run(args: argparse.Namespace) -> dict:
    process = None
    group_active = False
    cancelled_signal = None

    def cancel(signum, _frame):
        nonlocal cancelled_signal, group_active
        if cancelled_signal is None:
            cancelled_signal = signum
        if group_active:
            # 上位の強制終了より先にCLIを停止し、再入時は同じIDへ再送しない。
            group_active = False
            kill_group(process.pid, signal.SIGKILL)

    with open(args.stdin, "rb") as source, open(args.stdout, "wb") as out, open(args.stderr, "ab") as err:
        handlers = {sig: signal.signal(sig, cancel) for sig in CANCEL_SIGNALS}
        try:
            try:
                process = subprocess.Popen(args.command, cwd=args.cwd, stdin=source, stdout=out, stderr=err,
                                           start_new_session=True)
            except OSError as ex:
                code = 127 if isinstance(ex, FileNotFoundError) else 126 if isinstance(ex, PermissionError) else 1
                error = f"{type(ex).__name__}: {ex}"
                err.write(f"launch_error: {error}\n".encode())
                result = {"exit_code": code, "timeout": False, "launch_error": error}
            else:
                group_active = True
                if cancelled_signal is not None:
                    cancel(cancelled_signal, None)
                deadline = time.monotonic() + args.timeout
                timed_out = False
                while True:
                    # 回収とIDの無効化だけを保護し、待機中にはシグナルを遮断しない。
                    mask = signal.pthread_sigmask(signal.SIG_BLOCK, CANCEL_SIGNALS)
                    try:
                        code = process.poll()
                        if code is not None:
                            group_active = False
                    finally:
                        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
                    if code is not None:
                        break
                    if cancelled_signal is None and not timed_out and time.monotonic() >= deadline:
                        timed_out = True
                        stop_group(process)
                    time.sleep(0.02)
                result = {"exit_code": 124 if timed_out else code if code >= 0 else 128 - code,
                          "timeout": timed_out, "launch_error": None}
        finally:
            if group_active:
                group_active = False
                kill_group(process.pid, signal.SIGKILL)
                process.wait()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        if cancelled_signal is not None:
            result.update(exit_code=128 + cancelled_signal, timeout=False, cancel_signal=cancelled_signal)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--stdin", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command or args.timeout < 0:
        parser.error("command and a nonnegative timeout are required")
    result = run(args)
    print(json.dumps(result))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
