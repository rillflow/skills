"""cross-checkのQA用に公開イベントとCLI終了を別々に記録する。"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/cross-check/scripts'))
SPEC = importlib.util.spec_from_file_location(
    "cross_check_runner", ROOT / "skills/cross-check/scripts/run_cli.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
PUBLIC_BLOCKS = {"text", "input_text", "output_text", "tool_use", "tool_result"}
EVENT_FIELDS = {"type", "subtype", "uuid", "session_id", "model", "effort", "tool_name",
                "tool_use_id", "decision_reason_type", "is_error", "result", "modelUsage",
                "duration_ms", "usage", "skills", "tools", "cwd", "error",
                "api_error", "api_error_kind", "api_error_params", "api_error_status",
                "api_error_origin", "is_api_error_message", "apiError", "apiErrorParams",
                "isApiErrorMessage", "rate_limit_info"}
SIGNALS = (signal.SIGTERM, signal.SIGINT)


def public_blocks(value):
    if not isinstance(value, list):
        return value if isinstance(value, str) or value is None else None
    result = []
    for block in value:
        if not isinstance(block, dict) or block.get("type") not in PUBLIC_BLOCKS:
            continue
        kind = block["type"]
        fields = {"type", "text"} if kind in {"text", "input_text", "output_text"} else (
            {"type", "id", "name", "input"} if kind == "tool_use" else
            {"type", "tool_use_id", "is_error", "content"})
        item = {k: v for k, v in block.items() if k in fields}
        if kind == "tool_result":
            item["content"] = public_blocks(item.get("content"))
        result.append(item)
    return result


def public_event(event):
    """messageの形に関係なく、公開フィールドだけを保持する。"""
    if not isinstance(event, dict):
        return {"type": "capture_schema_error", "shape": type(event).__name__}
    if event.get("type") == "rate_limit_event":
        return {"type": "rate_limit_event", "rate_limit_event_raw": event}
    result = {k: v for k, v in event.items() if k in EVENT_FIELDS}
    if "message" in event:
        message = event["message"]
        if isinstance(message, dict):
            result["message"] = {k: v for k, v in message.items()
                                 if k in {"role", "model", "id", "usage"}}
            if "content" in message:
                result["message"]["content"] = public_blocks(message["content"])
        else:
            result["message"] = public_blocks(message)
    return result


def readback_evidence(original, returned_parts, references, *, numbered=False):
    """表示用行番号以外は変えず、保存本文の全行が返ったかを照合する。"""
    expected = original.splitlines()
    observed = []
    for part in returned_parts:
        for line in part.splitlines():
            observed.append(re.sub(r"^[ \t]*\d+(?:→|\t)", "", line) if numbered else line)
    complete = bool(expected) and any(
        observed[i:i + len(expected)] == expected
        for i in range(len(observed) - len(expected) + 1))
    return {"complete": complete, "total_lines": len(expected),
            "returned_lines": len(observed), "references": list(references),
            "original_sha256": hashlib.sha256(original.encode()).hexdigest()}


def peer_evidence(events, directory):
    """全Bash命令と生成物を並べる。実行数は片方だけで断定しない。"""
    commands = []
    for index, event in enumerate(events, 1):
        message = event.get("message") if isinstance(event, dict) else None
        blocks = message.get("content", []) if isinstance(message, dict) else []
        for block in blocks if isinstance(blocks, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                commands.append({"event": index, "command": block.get("input", {}).get("command")})
    root = Path(directory)
    metas = sorted(p.name.removesuffix(".meta.json") for p in root.glob("*.meta.json"))
    outputs = sorted(p.name.removesuffix(".out") for p in root.glob("*.out"))
    return {"bash_commands": commands, "meta_prefixes": metas, "out_prefixes": outputs,
            "paired": metas == outputs, "actual_calls": None,
            "review_required": "全命令と生成物を照合する。上書きや生成前の失敗は個数だけでは分からない。"}


def append_event(sink, event):
    sink.write(json.dumps(event, ensure_ascii=False) + "\n")
    sink.flush()


def capture(command, *, cwd, prompt_file, output_dir, timeout, env=None):
    """既存CLIの権限を変えず、検証を実行後に分離する。"""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    deadline = time.monotonic() + timeout
    errors = []
    cleanup_errors = []
    process = None
    group_owned = False
    cancelled = None
    timed_out = False
    final_result = None
    selector = selectors.DefaultSelector()

    def cancel(signum, _frame):
        nonlocal cancelled, group_owned
        if cancelled is None:
            cancelled = signum
        if group_owned:
            group_owned = False
            try:
                runner.kill_group(process.pid, signal.SIGKILL)
            except OSError as exc:
                cleanup_errors.append(f"cancel group: {type(exc).__name__}: {exc}")
                raise

    def consume(line, sink):
        nonlocal final_result
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            errors.append(type(exc).__name__)
            event = {"type": "capture_parse_error", "bytes": len(line)}
        try:
            event = public_event(event)
            if event.get("type") == "capture_schema_error":
                errors.append("event is not an object")
            event["captured_at"] = time.time()
            append_event(sink, event)
            if event.get("type") == "result":
                final_result = event
        except Exception as exc:
            # 通常の収集エラーでは読み取りを続ける。キャンセル時はループで停止する。
            errors.append(f"{type(exc).__name__}: {exc}")

    handlers = {s: signal.signal(s, cancel) for s in SIGNALS}
    try:
        with open(prompt_file, "rb") as source, (output / "stderr.log").open("wb") as err, (
                output / "events.jsonl").open("w", encoding="utf-8") as sink:
            process = subprocess.Popen(command, cwd=cwd, stdin=source, stdout=subprocess.PIPE,
                                       stderr=err, env=env, start_new_session=True)
            group_owned = True
            if cancelled is not None:
                cancel(cancelled, None)
            selector.register(process.stdout, selectors.EVENT_READ)
            pending = b""
            while selector.get_map() and cancelled is None:
                if timed_out and time.monotonic() >= deadline + runner.TERM_GRACE_SEC + 0.5:
                    cleanup_errors.append("stdout remained open after group shutdown")
                    break
                if not timed_out and cancelled is None and time.monotonic() >= deadline:
                    timed_out = True
                    runner.stop_group(process)
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    pending += chunk
                    while b"\n" in pending and cancelled is None:
                        line, pending = pending.split(b"\n", 1)
                        consume(line, sink)
            if pending and cancelled is None:
                consume(pending, sink)
            while cancelled is None:
                # 回収と所有権の解除を同時に行い、回収後のPIDへ送信しない。
                mask = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
                try:
                    code = process.poll()
                    if code is not None:
                        group_owned = False
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
                if code is not None:
                    break
                if not timed_out and cancelled is None and time.monotonic() >= deadline:
                    timed_out = True
                    runner.stop_group(process)
                time.sleep(0.02)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        if group_owned:
            group_owned = False
            try:
                runner.kill_group(process.pid, signal.SIGKILL)
            except OSError as exc:
                cleanup_errors.append(f"group: {type(exc).__name__}: {exc}")
        # グループへの送信権を失っても、直接子の回収責任は残る。
        if process is not None:
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError as exc:
                    cleanup_errors.append(f"caller: {type(exc).__name__}: {exc}")
                try:
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    cleanup_errors.append(f"caller reap: {type(exc).__name__}: {exc}")
        if process is not None and process.stdout is not None:
            process.stdout.close()
        selector.close()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    raw_code = process.returncode if process is not None else None
    result = {"caller_exit_code": raw_code if raw_code is None or raw_code >= 0 else 128 - raw_code,
              "caller_returncode": raw_code, "timeout": timed_out, "cancel_signal": cancelled,
              "collector_errors": errors, "cleanup_errors": cleanup_errors,
              "cleanup_complete": not cleanup_errors,
              "process_id": process.pid if process is not None else None,
              "owned_group_id": process.pid if process is not None else None,
              "started_at": started, "ended_at": time.time(),
              "result": final_result}
    (output / "capture.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if final_result and isinstance(final_result.get("result"), str):
        (output / "result.txt").write_text(final_result["result"])
    return result

def capture_policy(command, *, cwd, prompt_file, output_dir, timeout, primary_model):
    """新規callerのpre-tool上限だけに共通モデル方針を適用する。"""
    from types import SimpleNamespace
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    args = SimpleNamespace(command=list(command), cwd=str(cwd), stdin=str(prompt_file),
                           stdout=str(output / "policy.raw.json"), stderr=str(output / "stderr.log"),
                           timeout=timeout, primary_model=primary_model,
                           fallback_model="claude-opus-5-5", fallback_before_tools_only=True)
    cancelled = None
    def cancel(signum, _frame):
        nonlocal cancelled
        if cancelled is None:
            cancelled = signum
    args.external_cancel = lambda: cancelled
    handlers = {sig: signal.signal(sig, cancel) for sig in SIGNALS}
    try:
        state = runner.run(args)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    errors = []
    final_result = None
    attempts = state.get("attempts", [])
    sources = attempts or [{"stdout": args.stdout}]
    for number, attempt in enumerate(sources, 1):
        destination = output / f"attempt-{number}.events.jsonl"
        with destination.open("w", encoding="utf-8") as sink:
            try:
                lines = Path(attempt["stdout"]).read_bytes().splitlines()
            except OSError as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                lines = []
            for line in lines:
                try:
                    event = public_event(json.loads(line))
                    event["captured_at"] = time.time()
                    append_event(sink, event)
                    if number == len(sources) and event.get("type") == "result":
                        final_result = event
                except (ValueError, UnicodeError, OSError) as exc:
                    errors.append(f"{type(exc).__name__}: {exc}")
        attempt["public_events"] = str(destination)
    (output / "events.jsonl").write_bytes((output / f"attempt-{len(sources)}.events.jsonl").read_bytes())
    raw_code = state.get("cli_returncode")
    result = {"caller_exit_code": raw_code if raw_code is None or raw_code >= 0 else 128 - raw_code,
              "caller_returncode": raw_code, "policy_exit_code": state["exit_code"],
              "timeout": state["timeout"], "cancel_signal": state.get("cancel_signal", cancelled),
              "collector_errors": errors, "cleanup_errors": state.get("cleanup_errors", []),
              "cleanup_complete": state.get("group_cleanup_complete"),
              "process_reaped": state.get("process_reaped"), "process_id": state.get("pid"),
              "owned_group_id": state.get("pgid"), "started_at": started, "ended_at": time.time(),
              "result": final_result, "policy_result": state,
              "event_capture_mode": "public events extracted from each completed attempt raw file"}
    (output / "capture.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if final_result and isinstance(final_result.get("result"), str):
        (output / "result.txt").write_text(final_result["result"])
    return result
