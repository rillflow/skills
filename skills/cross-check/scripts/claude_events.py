"""Claudeの公開イベントを型検証し、回答と構造化された失敗理由を取り出す。"""
from __future__ import annotations

import json
import math
from pathlib import Path

FABLE_MODELS = frozenset(("claude-fable-5-1", "claude-fable-5"))
DEFAULT_FABLE = "claude-fable-5-1"
DEFAULT_OPUS = "claude-opus-5-5"


def same_json(left, right):
    """JSON値の型・配列順を保ち、オブジェクトのキー順だけを無視する。"""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same_json(a, b) for a, b in zip(left, right))
    return left == right


def nonnegative_finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def safe_rate_event(event, terminal_session):
    """モデル別credits要求に伴う既知の診断だけを許可する。"""
    if set(event) - {"type", "uuid", "session_id", "rate_limit_info"}:
        return False
    if (not isinstance(event.get("session_id"), str) or not event["session_id"].strip()
            or event["session_id"] != terminal_session
            or ("uuid" in event and not isinstance(event["uuid"], str))):
        return False
    info = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return False
    allowed = {"status", "resetsAt", "rateLimitType", "overageStatus", "overageDisabledReason",
               "isUsingOverage", "unifiedWindows", "utilization", "surpassedThreshold", "overageEnabled"}
    if set(info) - allowed:
        return False
    if (any(not isinstance(info.get(k), str) for k in ("status", "rateLimitType", "overageStatus", "overageDisabledReason"))
            or info.get("status") not in {"allowed", "allowed_warning", "rejected"}
            or info.get("rateLimitType") not in {"five_hour", "seven_day", "seven_day_overage_included"}
            or info.get("overageStatus") != "rejected"
            or info.get("overageDisabledReason") != "org_level_disabled"
            or info.get("isUsingOverage") is not False
            or ("overageEnabled" in info and info["overageEnabled"] is not False)):
        return False
    if any(k in info and not nonnegative_finite(info[k]) for k in ("resetsAt", "utilization", "surpassedThreshold")):
        return False
    windows = info.get("unifiedWindows", {})
    if not isinstance(windows, dict) or set(windows) - {"five_hour", "seven_day", "seven_day_overage_included"}:
        return False
    for window in windows.values():
        if (not isinstance(window, dict)
                or set(window) - {"utilization", "resetsAt", "status", "surpassedThreshold"}
                or not isinstance(window.get("status"), str)
                or window.get("status") not in {"allowed", "allowed_warning", "rejected"}
                or any(k in window and not nonnegative_finite(window[k]) for k in ("utilization", "resetsAt", "surpassedThreshold"))):
            return False
    return True


def read_claude(path: str) -> dict:
    result = {"body": "", "models": [], "session_id": None, "is_error": False,
              "error_kind": None, "rate_limit_info": None, "valid": False,
              "error_details": [], "subtype": None, "tools_seen": False,
              "mixed_sessions": False, "schema_errors": [], "error_status": None,
              "error_origin": None, "is_api_error_message": False,
              "error_params_empty": None, "rate_limit_events": [], "rate_limit_events_safe": True}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as ex:
        result["schema_errors"].append(type(ex).__name__)
        return result
    try:
        records = [json.loads(text)]
    except ValueError:
        records = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                result["schema_errors"].append("invalid JSON event")
                return result
    def alias_value(event, keys, expected):
        values = [event[k] for k in keys if k in event and event[k] is not None]
        if any(type(v) is not expected for v in values) or any(not same_json(v, values[0]) for v in values[1:]):
            result["schema_errors"].append("conflicting or invalid aliases: " + ",".join(keys))
            return None
        return values[0] if values else None

    public_text = []
    session_ids = set()
    terminal = None
    failure_fields = {}

    def failure_value(field, value):
        if value is None:
            return
        if field in failure_fields and not same_json(failure_fields[field], value):
            result["schema_errors"].append("conflicting failure events: " + field)
            return
        failure_fields[field] = value
        result[field] = value

    for event in records:
        if not isinstance(event, dict):
            result["schema_errors"].append("event must be an object")
            continue
        if event.get("type") == "rate_limit_event":
            if "rate_limit_event_raw" in event:
                timestamp = event.get("captured_at")
                envelope_ok = (not (set(event) - {"type", "rate_limit_event_raw", "captured_at"})
                               and ("captured_at" not in event or nonnegative_finite(timestamp)))
                raw = event["rate_limit_event_raw"]
                if envelope_ok and isinstance(raw, dict) and raw.get("type") == "rate_limit_event":
                    result["rate_limit_events"].append(raw)
                else:
                    result["rate_limit_events"].append(event)
            else:
                result["rate_limit_events"].append(event)
            continue
        sid = event.get("session_id")
        if sid is not None:
            if not isinstance(sid, str):
                result["schema_errors"].append("session_id must be text")
            elif sid:
                session_ids.add(sid)
        if "type" in event and not isinstance(event["type"], str):
            result["schema_errors"].append("event type must be text")
        if event.get("type") == "system" and "model" in event and not isinstance(event["model"], str):
            result["schema_errors"].append("system model must be text")
        kind = alias_value(event, ("api_error", "api_error_kind", "apiError"), str)
        params = alias_value(event, ("api_error_params", "apiErrorParams"), dict)
        status = alias_value(event, ("api_error_status",), int)
        origin = alias_value(event, ("api_error_origin",), str)
        api_message = alias_value(event, ("is_api_error_message", "isApiErrorMessage"), bool)
        failure_value("error_status", status)
        failure_value("error_origin", origin)
        failure_value("is_api_error_message", api_message)
        if kind is not None:
            if not isinstance(kind, str):
                result["schema_errors"].append("api error kind must be text")
            else:
                failure_value("error_kind", kind)
        if params is not None:
            failure_value("error_params_empty", not params)
            if not isinstance(params, dict):
                result["schema_errors"].append("api error params must be an object")
            elif params.get("rate_limit_info") is not None:
                info = params["rate_limit_info"]
                if not isinstance(info, dict):
                    result["schema_errors"].append("rate_limit_info must be an object")
                else:
                    failure_value("rate_limit_info", info)
        error = event.get("error")
        if isinstance(error, str):
            result["error_details"].append(error)
        elif error is not None:
            result["schema_errors"].append("error must be text")
        if event.get("type") == "result" or ("result" in event and "type" not in event):
            terminal = event
            if "result" in event and not isinstance(event["result"], str):
                result["schema_errors"].append("terminal result must be text")
            usage = event.get("modelUsage", {})
            if not isinstance(usage, dict) or any(not isinstance(v, dict) for v in usage.values()):
                result["schema_errors"].append("modelUsage must contain objects")
            if "is_error" in event and type(event["is_error"]) is not bool:
                result["schema_errors"].append("is_error must be boolean")
        if event.get("type") == "assistant" and "message" in event:
            message = event["message"]
            if not isinstance(message, dict):
                result["schema_errors"].append("assistant message must be an object")
                continue
            content = message.get("content", [])
            if not isinstance(content, list):
                result["schema_errors"].append("assistant content must be an array")
                continue
            for block in content:
                if not isinstance(block, dict):
                    result["schema_errors"].append("content block must be an object")
                    continue
                if block.get("type") == "tool_use":
                    result["tools_seen"] = True
                elif block.get("type") == "text":
                    if isinstance(block.get("text"), str):
                        public_text.append(block["text"])
                    else:
                        result["schema_errors"].append("text block must contain text")
    result["mixed_sessions"] = len(session_ids) > 1
    if terminal is not None and not result["schema_errors"]:
        result.update(valid=True, is_error=bool(terminal.get("is_error")),
                      subtype=terminal.get("subtype"), session_id=terminal.get("session_id"))
        result["body"] = terminal.get("result", "\n".join(public_text))
        usage = terminal.get("modelUsage", {})
        result["models"] = sorted(k for k in usage if not k.startswith("<"))
    result["rate_limit_events_safe"] = all(
        safe_rate_event(event, result["session_id"]) for event in result["rate_limit_events"])
    return result


def quota_fallback_reason(parsed: dict, model: str) -> str | None:
    """既存名を維持し、Fableの使用上限とモデル別credits要求を区別する。"""
    info = parsed.get("rate_limit_info")
    if (model not in FABLE_MODELS or not parsed.get("valid") or not parsed.get("is_error")
            or parsed.get("mixed_sessions") or parsed.get("is_api_error_message") is not True
            or parsed.get("error_status") != 429 or parsed.get("error_origin") != "first_party"):
        return None
    if parsed.get("error_kind") == "model_requires_usage_credits":
        params_empty = parsed.get("error_params_empty")
        session_id = parsed.get("session_id")
        if (isinstance(session_id, str) and session_id.strip()
                and info is None and parsed.get("rate_limit_events_safe", False)
                and (params_empty is None or params_empty is True)
                and all(error in {"rate_limit", "model_requires_usage_credits"}
                        for error in parsed.get("error_details", []))
                and parsed.get("models", []) in ([], [model])):
            return "fable_request_model_requires_usage_credits"
        return None
    plain_limit = isinstance(info, dict) and all(
        info.get(k) is None for k in ("overageStatus", "overageDisabledReason", "limitScope"))
    disabled_extra_usage = (isinstance(info, dict) and info.get("overageStatus") == "rejected"
                            and info.get("overageDisabledReason") == "org_level_disabled"
                            and info.get("limitScope") is None)
    if (parsed.get("error_kind") != "usage_limit_reached"
            or not isinstance(info, dict) or info.get("status") != "rejected"
            or info.get("isUsingOverage") is not False
            or not isinstance(info.get("rateLimitType"), str)
            or info["rateLimitType"] not in {"five_hour", "seven_day"}
            or not (plain_limit or disabled_extra_usage)):
        return None
    return "fable_request_usage_limit"
