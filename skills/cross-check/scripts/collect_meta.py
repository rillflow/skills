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
    try:
        with open(raw, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as ex:
        if not timed_out:
            add_note(meta, f"claude output is not JSON: {ex}")
        return None
    if not isinstance(data, dict):
        return None
    sid = data.get("session_id")
    meta["session_id"] = sid
    usage = data.get("modelUsage") or {}
    if usage:
        meta["model_observed"] = ",".join(sorted(usage))
        meta["model_observed_source"] = "claude --output-format json modelUsage"
    transcripts = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl")) if sid else []
    if transcripts:
        meta["transcript"] = transcripts[0]
        for m in re.finditer(r'"effort":"([a-z]+)"', read_text(transcripts[0])):
            meta["effort"] = m.group(1)
        if meta["effort"]:
            meta["effort_source"] = "claude transcript effort field (last)"
    with open(out, "w", encoding="utf-8") as f:
        f.write(data.get("result") or "")
    if data.get("is_error"):
        add_note(meta, f"claude reported is_error=true subtype={data.get('subtype')}")
        return 1
    return None


def collect(args: argparse.Namespace) -> tuple[dict, int]:
    exit_code = args.exit_code
    timed_out = args.timed_out
    runner_note = None
    if args.runner_result is not None:
        try:
            result = json.loads(args.runner_result)
            if (not isinstance(result, dict) or type(result.get("exit_code")) is not int
                    or result["exit_code"] != exit_code or not 0 <= exit_code <= 255
                    or type(result.get("timeout")) is not bool
                    or (result["timeout"] and exit_code != 124)
                    or (result.get("cancel_signal") is not None and
                        (type(result["cancel_signal"]) is not int or result["cancel_signal"] not in (2, 15)
                         or result["timeout"] or exit_code != 128 + result["cancel_signal"]))
                    or (result.get("launch_error") is not None and
                        (not isinstance(result["launch_error"], str) or exit_code == 0))):
                raise ValueError("inconsistent exit_code, timeout or launch_error")
            timed_out = result["timeout"]
            if result.get("launch_error"):
                runner_note = "launch_error: " + result["launch_error"]
            if result.get("cancel_signal"):
                runner_note = (runner_note + "; " if runner_note else "") + f"cancelled by signal {result['cancel_signal']}"
        except (ValueError, TypeError) as ex:
            runner_note = f"invalid runner result (helper exit {exit_code}): {ex}"
            exit_code = exit_code or 1
            timed_out = False
    meta = {
        "side": args.side,
        "model_requested": args.model or None,
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
    }
    errtxt = read_text(args.err)
    if args.side == "codex":
        collect_codex(meta, args.out, errtxt, args.hook_reply)
    else:
        forced = collect_claude(meta, args.out, args.raw, timed_out)
        if forced is not None and exit_code == 0:
            exit_code = forced

    body = read_text(args.out)
    output_unverified = args.side == "codex" and is_hook_reply(body.strip(), args.hook_reply)
    if output_unverified:
        add_note(meta, "hook check reply could not be matched to a preceding final response; original .out preserved")
    requested, observed = meta["model_requested"], meta["model_observed"]
    if requested and observed:
        meta["model_mismatch"] = not any(requested == o or requested in o for o in observed.split(","))
    reject = REJECT.search(errtxt + "\n" + body[:2000])
    if reject and exit_code != 0:
        meta["model_mismatch"] = True
        add_note(meta, "model rejected: " + reject.group(0))
    if meta["effort"] is not None:
        meta["effort_mismatch"] = meta["effort"] != meta["effort_requested"]

    if timed_out:
        meta["status"], exit_code = "timeout", 124
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
