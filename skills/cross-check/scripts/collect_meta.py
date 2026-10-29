#!/usr/bin/env python3
"""ask.shの実行結果から応答本文とmetaを確定する。

Codexはrollout、Claudeは--output-format jsonの出力とセッション記録から、
実際に使われたモデルとeffortを読み取る。終了コードはask.shの規約に従う。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

from claude_events import read_claude, DEFAULT_OPUS, FABLE_MODELS

DEFAULT_HOOK_REPLY = r"点検しました|修正が必要な箇所はありません|점검했습니다|고칠 것이 없습니다"
MAX_HOOK_REPLY_CHARS = 400
REJECT = re.compile(
    r"unrecognized_model|issue with the selected model|model is not supported"
    r"|Model metadata for `[^`]+` not found"
)


def read_text(path: str, limit: int | None = None) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read() if limit is None else f.read(limit)
    except OSError:
        return ""


def codex_rollout(session_id: str) -> str | None:
    pattern = os.path.expanduser(f"~/.codex/sessions/*/*/*/rollout-*{session_id}.jsonl")
    files = glob.glob(pattern)
    return files[0] if files else None


def read_rollout(path: str) -> tuple[str | None, str | None, list[str]]:
    model = effort = None
    messages: list[str] = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            payload = rec.get("payload") or {}
            channel, phase = payload.get("channel"), payload.get("phase")
            final = ((channel == "final" and phase in (None, "final_answer"))
                     or (channel is None and phase == "final_answer"))
            if (rec.get("type") == "response_item" and payload.get("type") == "message"
                    and payload.get("role") == "assistant" and final):
                text = "".join(
                    c.get("text", "") for c in payload.get("content", [])
                    if isinstance(c, dict) and c.get("type") == "output_text"
                ).strip()
                if text:
                    messages.append(text)
            if rec.get("type") == "turn_context":
                model = payload.get("model") or model
                effort = payload.get("effort") or effort
    return model, effort, messages


def add_note(meta: dict, note: str) -> None:
    meta["note"] = f"{meta['note']}; {note}" if meta["note"] else note


def collect_codex(meta: dict, out: str, errtxt: str, hook_reply: str) -> None:
    header_model = re.search(r"^model:\s*(\S+)", errtxt, re.M)
    session = re.search(r"^session id:\s*(\S+)", errtxt, re.M)
    if session:
        meta["session_id"] = session.group(1)
        rollout = codex_rollout(session.group(1))
        if rollout:
            meta["rollout_file"] = rollout
            model, effort, messages = read_rollout(rollout)
            if effort:
                meta["effort"] = effort
                meta["effort_source"] = "rollout turn_context.payload.effort"
            if model:
                meta["model_observed"] = model
                meta["model_observed_source"] = "rollout turn_context.payload.model"
                if header_model and header_model.group(1) != model:
                    add_note(meta, f"stderr header model={header_model.group(1)} differs from rollout")
            replace_hook_reply(meta, out, messages, hook_reply)
    if meta["model_observed"] is None and header_model:
        meta["model_observed"] = header_model.group(1)
        meta["model_observed_source"] = "stderr header"
    if meta["effort"] is None:
        r = re.search(r"^reasoning effort:\s*(\S+)", errtxt, re.M)
        if r:
            meta["effort"] = r.group(1)
            meta["effort_source"] = "stderr header reasoning effort"


def is_hook_reply(text: str, hook_reply: str) -> bool:
    """点検の定型句と句読点だけで構成された短い応答を判定する。"""
    return bool(text and len(text) < MAX_HOOK_REPLY_CHARS
                and re.fullmatch(rf"(?:(?:{hook_reply})[\s。.!！?？]*)+", text))


def replace_hook_reply(meta: dict, out: str, messages: list[str], hook_reply: str) -> None:
    """最後のfinalが点検の返答と一致する場合だけ、直前のfinalを復元する。"""
    last = read_text(out).strip()
    if not is_hook_reply(last, hook_reply) or not messages or messages[-1] != last:
        return
    bodies = [m for m in messages[:-1] if not is_hook_reply(m, hook_reply)]
    if not bodies:
        return
    os.replace(out, out + ".hook")
    with open(out, "w", encoding="utf-8") as f:
        f.write(bodies[-1] + "\n")
    add_note(meta, "last message was a hook check reply; .out holds the preceding final response, original in .out.hook")


def collect_claude(meta: dict, out: str, raw: str, timed_out: bool) -> int | None:
    meta["raw"] = raw
    parsed = read_claude(raw)
    if not parsed["valid"]:
        meta["claude_error"] = {k: parsed[k] for k in (
            "error_kind", "error_status", "error_origin", "is_api_error_message",
            "rate_limit_info", "rate_limit_events", "rate_limit_events_safe", "error_details", "is_error", "valid", "schema_errors", "error_params_empty")}
        if not timed_out:
            add_note(meta, "claude output has no valid terminal result: " + ", ".join(parsed["schema_errors"]))
        return None
    if not parsed["is_error"] and is_hook_reply(parsed["body"].strip(), DEFAULT_HOOK_REPLY):
        recovered = read_claude(meta["err"])
        if (recovered["valid"] and not recovered["mixed_sessions"] and not parsed["mixed_sessions"]
                and not recovered["is_error"] and recovered["body"].strip()
                and not is_hook_reply(recovered["body"].strip(), DEFAULT_HOOK_REPLY)
                and recovered["session_id"] and recovered["session_id"] == parsed["session_id"]
                and recovered["models"] and recovered["models"] == parsed["models"]):
            parsed = recovered
            add_note(meta, "hook reply replaced by an attested terminal result from structured stderr; raw sources preserved")
    sid = parsed["session_id"]
    meta["session_id"] = sid
    meta["claude_error"] = {k: parsed[k] for k in (
        "error_kind", "error_status", "error_origin", "is_api_error_message",
        "rate_limit_info", "rate_limit_events", "rate_limit_events_safe", "error_details", "is_error", "valid", "schema_errors", "error_params_empty")}
    if parsed["models"]:
        meta["model_observed"] = ",".join(parsed["models"])
        meta["model_observed_source"] = "claude terminal result modelUsage"
    transcripts = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl")) if sid else []
    if transcripts:
        meta["transcript"] = transcripts[0]
        for m in re.finditer(r'"effort":"([a-z]+)"', read_text(transcripts[0])):
            meta["effort"] = m.group(1)
        if meta["effort"]:
            meta["effort_source"] = "claude transcript effort field (last)"
    with open(out, "w", encoding="utf-8") as f:
        f.write(parsed["body"])
    if parsed["is_error"]:
        add_note(meta, f"claude reported is_error=true subtype={parsed['subtype']}")
        return 1
    return None


def collect(args: argparse.Namespace) -> tuple[dict, int]:
    exit_code = args.exit_code
    timed_out = args.timed_out
    runner_note = None
    runner = None
    runner_state = "not_provided"
    if args.runner_result is not None:
        try:
            result = json.loads(args.runner_result)
            if (not isinstance(result, dict) or type(result.get("exit_code")) is not int
                    or result["exit_code"] != exit_code or not 0 <= exit_code <= 255
                    or type(result.get("timeout")) is not bool
                    or (result["timeout"] and exit_code != 124)
                    or (result.get("cancel_signal") is not None and
                        (type(result["cancel_signal"]) is not int or result["cancel_signal"] not in (2, 15)
                         or (not result["timeout"] and exit_code != 128 + result["cancel_signal"])))
                    or (result.get("launch_error") is not None and
                        (not isinstance(result["launch_error"], str) or exit_code == 0))):
                raise ValueError("inconsistent exit_code, timeout or launch_error")
            runner = result
            runner_state = "valid"
            timed_out = result["timeout"]
            if result.get("launch_error"):
                runner_note = "launch_error: " + result["launch_error"]
            if result.get("cancel_signal"):
                runner_note = (runner_note + "; " if runner_note else "") + f"cancelled by signal {result['cancel_signal']}"
        except (ValueError, TypeError) as ex:
            runner_note = f"invalid runner result (helper exit {exit_code}): {ex}"
            exit_code = exit_code or 1
            timed_out = None
            runner_state = "invalid"
    meta = {
        "side": args.side,
        "model_requested": (args.model if args.model_requested is None else args.model_requested) or None,
        "model_primary": args.model or None,
        "model_observed": None,
        "model_observed_source": None,
        "model_mismatch": None,
        "effort_requested": args.effort,
        "effort": None,
        "effort_source": None,
        "effort_mismatch": None,
        "session_id": None,
        "exit_code": exit_code,
        "timeout": timed_out,
        "timeout_sec": args.timeout,
        "started_at": args.started_at,
        "ended_at": args.ended_at,
        "elapsed_sec": args.elapsed,
        "prompt_file": os.path.abspath(args.prompt_file),
        "cwd": args.cwd,
        "out": args.out,
        "err": args.err,
        "status": None,
        "note": runner_note,
        "runner_result_raw": args.runner_result,
        "runner_state": runner_state,
        "cleanup_complete": runner.get("group_cleanup_complete") if runner else None,
        "process_reaped": runner.get("process_reaped") if runner else None,
        "cleanup_errors": runner.get("cleanup_errors", []) if runner else [],
        "execution": {k: runner.get(k) for k in ("pid", "pgid", "euid", "runner_pid", "runtime", "executable")} if runner else None,
        "attempts": runner.get("attempts", []) if runner else [],
        "model_attempted": args.model or None,
        "approved_fallback": False,
        "fallback_reason": None,
        "artifact_errors": runner.get("artifact_errors", []) if runner else [],
        "cli_returncode": runner.get("cli_returncode") if runner else None,
    }
    errtxt = read_text(args.err)
    if args.side == "codex":
        collect_codex(meta, args.out, errtxt, args.hook_reply)
    else:
        forced = collect_claude(meta, args.out, args.raw, timed_out)
        if forced is not None and exit_code == 0:
            exit_code = forced

    body = read_text(args.out)
    output_unverified = is_hook_reply(body.strip(), args.hook_reply)
    if output_unverified:
        add_note(meta, "hook check reply could not be matched to a preceding final response; original .out preserved")
    attempts = meta["attempts"]
    if (runner and runner.get("fallback_used") is True and len(attempts) == 2
            and args.side == "claude" and args.model in FABLE_MODELS
            and attempts[0].get("model_requested") == args.model
            and attempts[1].get("model_requested") == DEFAULT_OPUS
            and runner.get("fallback_model") == DEFAULT_OPUS):
        meta["approved_fallback"] = True
        meta["fallback_reason"] = attempts[0].get("fallback_reason")
        meta["model_attempted"] = DEFAULT_OPUS
    requested, observed = meta["model_attempted"], meta["model_observed"]
    if requested and observed:
        meta["model_mismatch"] = requested not in observed.split(",")
    reject = REJECT.search(errtxt + "\n" + body[:2000])
    if reject and exit_code != 0:
        meta["model_mismatch"] = True
        add_note(meta, "model rejected: " + reject.group(0))
    if meta["effort"] is not None:
        meta["effort_mismatch"] = meta["effort"] != meta["effort_requested"]

    if timed_out:
        meta["status"], exit_code = "timeout", 124
    elif meta["artifact_errors"]:
        meta["status"] = "error"
        exit_code = exit_code or 1
    elif runner_state == "invalid":
        meta["status"] = "error"
    elif runner and (runner.get("cleanup_errors") or (runner.get("pid") is not None and runner.get("process_reaped") is False)
                     or runner.get("group_cleanup_complete") is False):
        meta["status"] = "cleanup_error"
        exit_code = exit_code or 1
    elif meta["model_mismatch"] is True:
        meta["status"], exit_code = "model_mismatch", 5
    elif exit_code != 0:
        meta["status"] = "error"
    elif not body.strip():
        meta["status"], exit_code = "empty_output", 3
    elif observed is None:
        meta["status"], exit_code = "model_unverified", 4
    elif output_unverified:
        meta["status"], exit_code = "output_unverified", 6
    else:
        meta["status"] = "ok"
    meta["exit_code"] = exit_code
    return meta, exit_code


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--side", choices=["codex", "claude"], required=True)
    p.add_argument("--model", default="")
    p.add_argument("--model-requested", default=None)
    p.add_argument("--effort", required=True)
    p.add_argument("--exit-code", type=int, required=True)
    p.add_argument("--timed-out", action="store_true")
    p.add_argument("--runner-result")
    p.add_argument("--timeout", type=int, required=True)
    p.add_argument("--started-at", required=True)
    p.add_argument("--ended-at", required=True)
    p.add_argument("--elapsed", type=int, required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--err", required=True)
    p.add_argument("--raw", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--hook-reply", default=os.environ.get("CROSS_CHECK_HOOK_REPLY", DEFAULT_HOOK_REPLY))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    meta, code = collect(args)
    with open(args.meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(args.meta)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
