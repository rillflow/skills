#!/usr/bin/env python3
"""CLIを専用プロセスグループで実行し、失敗と終了処理の状態をJSONで返す。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from claude_events import read_claude, quota_fallback_reason

CANCEL_SIGNALS = (signal.SIGTERM, signal.SIGINT)
TERM_GRACE_SEC = 2.0
REAP_TIMEOUT_SEC = 2.0
GROUP_CHECK_SEC = 0.5


def kill_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def stop_group(process: subprocess.Popen) -> None:
    # 回収前のリーダーIDを保持したまま、同じグループの子孫も停止する。
    kill_group(process.pid, signal.SIGTERM)
    time.sleep(TERM_GRACE_SEC)
    kill_group(process.pid, signal.SIGKILL)


def run_once(args: argparse.Namespace) -> dict:
    process = None
    group_active = False
    cancelled_signal = None
    result = {"exit_code": 1, "timeout": False, "launch_error": None,
              "process_reaped": False, "group_cleanup_complete": None,
              "cleanup_errors": [], "pid": None, "pgid": None,
              "euid": os.geteuid(), "runner_pid": os.getpid(),
              "runtime": {"python": platform.python_version(), "system": platform.system()},
              "executable": os.path.realpath(shutil.which(args.command[0]) or args.command[0])}

    def record(operation, ex, target):
        result["cleanup_errors"].append({"operation": operation, "target": target,
                                         "error": type(ex).__name__, "errno": getattr(ex, "errno", None)})

    def send_group(sig):
        try:
            kill_group(process.pid, sig)
            return True
        except ProcessLookupError:
            return True
        except OSError as ex:
            record("killpg", ex, process.pid)
            return False

    def cancel(signum, _frame):
        nonlocal cancelled_signal, group_active
        if cancelled_signal is None:
            cancelled_signal = signum
        if group_active:
            # 再入と回収後のID再利用を避け、所有するグループに一度だけ送信する。
            group_active = False
            result["cancel_group_signal_ok"] = send_group(signal.SIGKILL)

    def stop(timed_out):
        nonlocal group_active
        if timed_out and group_active:
            send_group(signal.SIGTERM)
            end = time.monotonic() + TERM_GRACE_SEC
            while cancelled_signal is None and time.monotonic() < end:
                time.sleep(0.02)
        if group_active:
            group_active = False
            killed = send_group(signal.SIGKILL)
        else:
            killed = result.get("cancel_group_signal_ok", False)
        if not killed and process.returncode is None:
            # Popenが保持する未回収の直接子だけを対象にする。子孫の終了は保証しない。
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except OSError as ex:
                record("kill_direct_child", ex, process.pid)
        try:
            process.wait(timeout=REAP_TIMEOUT_SEC)
            result["process_reaped"] = True
        except (OSError, subprocess.TimeoutExpired) as ex:
            record("wait_direct_child", ex, process.pid)

    def check_group():
        # signal 0は存在確認だけであり、回収済みのIDには終了シグナルを送らない。
        end = time.monotonic() + GROUP_CHECK_SEC
        while True:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return True
            except OSError as ex:
                record("probe_group", ex, process.pid)
                return None
            if time.monotonic() >= end:
                return False
            time.sleep(0.02)

    handlers = {}
    try:
        with tempfile.TemporaryFile() as source, open(args.stdout, "wb") as out, open(args.stderr, "ab") as err:
            prompt = args.input_bytes if hasattr(args, "input_bytes") else Path(args.stdin).read_bytes()
            source.write(prompt)
            source.seek(0)
            result["prompt_sha256"] = hashlib.sha256(prompt).hexdigest()
            handlers = {sig: signal.signal(sig, cancel) for sig in CANCEL_SIGNALS}
            try:
                process = subprocess.Popen(args.command, cwd=args.cwd, stdin=source, stdout=out, stderr=err,
                                           start_new_session=True)
            except OSError as ex:
                result.update(exit_code=127 if isinstance(ex, FileNotFoundError) else
                              126 if isinstance(ex, PermissionError) else 1,
                              launch_error=f"{type(ex).__name__}: {ex}")
                err.write(f"launch_error: {result['launch_error']}\n".encode())
            else:
                result.update(pid=process.pid, pgid=process.pid)
                group_active = True
                if cancelled_signal is not None:
                    cancel(cancelled_signal, None)
                deadline = getattr(args, "deadline", time.monotonic() + args.timeout)
                while True:
                    if cancelled_signal is not None:
                        stop(False)
                        break
                    if time.monotonic() >= deadline:
                        result["timeout"] = True
                        stop(True)
                        break
                    mask = signal.pthread_sigmask(signal.SIG_BLOCK, CANCEL_SIGNALS)
                    try:
                        code = process.poll()
                        if code is not None:
                            group_active = False
                            result["process_reaped"] = True
                    finally:
                        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
                    if code is not None:
                        result["exit_code"] = code if code >= 0 else 128 - code
                        break
                    time.sleep(0.02)
                result["group_cleanup_complete"] = check_group()
    except (OSError, subprocess.SubprocessError) as ex:
        result["runner_error"] = f"{type(ex).__name__}: {ex}"
        if process is not None and group_active:
            stop(False)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    result["cli_returncode"] = process.returncode if process is not None else None
    if cancelled_signal is not None:
        result.update(exit_code=124 if result["timeout"] else 128 + cancelled_signal, cancel_signal=cancelled_signal)
    elif result["timeout"]:
        result["exit_code"] = 124
    elif result["exit_code"] == 0 and (not result["process_reaped"] or
                                        result["group_cleanup_complete"] is not True or
                                        result["cleanup_errors"]):
        result["exit_code"] = 1
    return result


def run(args: argparse.Namespace) -> dict:
    if not getattr(args, "fallback_model", None):
        return run_once(args)
    deadline = time.monotonic() + args.timeout
    requested = args.primary_model
    try:
        prompt = Path(args.stdin).read_bytes()
    except OSError:
        return run_once(args)
    prompt_sha256 = hashlib.sha256(prompt).hexdigest()
    attempts = []
    final = None
    for number, model in enumerate((requested, args.fallback_model), 1):
        remaining = deadline - time.monotonic()
        if number > 1 and getattr(args, "external_cancel", lambda: None)() is not None:
            final["fallback_decision"] = "cancelled_before_retry"
            break
        if number > 1 and remaining <= 0:
            final["fallback_decision"] = "deadline_exhausted"
            break
        prefix = args.stdout.removesuffix(".raw.json")
        attempt_args = argparse.Namespace(**vars(args))
        attempt_args.stdout = f"{prefix}.attempt-{number}.raw.json"
        attempt_args.stderr = f"{prefix}.attempt-{number}.err"
        attempt_args.input_bytes = prompt
        attempt_args.timeout = max(0, remaining)
        attempt_args.deadline = deadline
        attempt_args.command = list(args.command)
        attempt_args.command[attempt_args.command.index("--model") + 1] = model
        started = time.monotonic()
        state = run_once(attempt_args)
        parsed = read_claude(attempt_args.stdout)
        body_path = f"{prefix}.attempt-{number}.out"
        artifact_errors = []
        try:
            Path(body_path).write_text(parsed["body"], encoding="utf-8")
            shutil.copyfile(attempt_args.stdout, args.stdout)
            shutil.copyfile(attempt_args.stderr, args.stderr)
        except OSError as ex:
            artifact_errors.append(f"{type(ex).__name__}: {ex}")
        reason = quota_fallback_reason(parsed, model)
        attempts.append({**state, "number": number, "model_requested": model,
                         "model_observed": parsed["models"], "error_kind": parsed["error_kind"],
                         "rate_limit_info": parsed["rate_limit_info"],
                         "rate_limit_events": parsed["rate_limit_events"],
                         "rate_limit_events_safe": parsed["rate_limit_events_safe"],
                         "quota_eligible": reason == "fable_request_usage_limit",
                         "fallback_eligible": reason is not None, "fallback_reason": reason,
                         "stdout": attempt_args.stdout, "stderr": attempt_args.stderr, "out": body_path,
                         "elapsed_sec": round(time.monotonic() - started, 3),
                         "prompt_sha256": prompt_sha256, "artifact_errors": artifact_errors})
        final = dict(state)
        if artifact_errors:
            final.update(artifact_errors=artifact_errors, exit_code=state["exit_code"] or 1)
            break
        safe = state["process_reaped"] and state["group_cleanup_complete"] is True and not state["cleanup_errors"]
        if number == 2 or (state["exit_code"] == 0 and not parsed["is_error"]) or state["timeout"] or state.get("cancel_signal") or not reason or not safe or (getattr(args, "fallback_before_tools_only", False) and parsed["tools_seen"]):
            break
    final.update(attempts=attempts, model_requested=requested,
                 fallback_model=args.fallback_model, fallback_used=len(attempts) == 2)
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--stdin", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--primary-model")
    parser.add_argument("--fallback-model")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command or args.timeout < 0:
        parser.error("command and a nonnegative timeout are required")
    outer_cancel = None
    def cancel_between_attempts(signum, _frame):
        nonlocal outer_cancel
        if outer_cancel is None:
            outer_cancel = signum
    args.external_cancel = lambda: outer_cancel
    handlers = {sig: signal.signal(sig, cancel_between_attempts) for sig in CANCEL_SIGNALS}
    try:
        try:
            result = run(args)
        except (OSError, subprocess.SubprocessError) as ex:
            result = {"exit_code": 1, "timeout": None, "launch_error": None,
                      "runner_error": f"{type(ex).__name__}: {ex}",
                      "process_reaped": None, "group_cleanup_complete": None, "cleanup_errors": []}
        if outer_cancel is not None and not result.get("cancel_signal"):
            result.update(cancel_signal=outer_cancel,
                          exit_code=124 if result.get("timeout") else 128 + outer_cancel)
        print(json.dumps(result))
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
