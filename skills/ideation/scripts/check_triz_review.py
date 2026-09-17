#!/usr/bin/env python3
"""Validate the structural completeness of one full-TRIZ review JSON file.

This checker deliberately does not judge the quality, feasibility, or factual
accuracy of the review's reasoning.  It only checks the declared JSON contract.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class ContractError(Exception):
    """Raised when an input does not meet the review contract."""


SEARCH_RECORD_REQUIRED_TOOL_IDS = {"function-oriented-search", "analogue-problems"}


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"JSONキーが重複しています: {key}")
        result[key] = value
    return result


def load_json(path: Path, label: str) -> Any:
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source, object_pairs_hook=no_duplicate_object)
    except FileNotFoundError as error:
        raise ContractError(f"{label}ファイルがありません: {path}") from error
    except UnicodeDecodeError as error:
        raise ContractError(f"{label}はUTF-8のJSONである必要があります: {path}") from error
    except json.JSONDecodeError as error:
        raise ContractError(f"{label}のJSONが不正です: {error.msg}") from error
    except OSError as error:
        raise ContractError(f"{label}ファイルを読み込めません: {path} ({error.strerror or error})") from error


def require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{path}はオブジェクトである必要があります")
    return value


def require_string(value: Any, path: str, *, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{path}は文字列である必要があります")
    if not allow_blank and not value.strip():
        raise ContractError(f"{path}は空にできません")
    return value


def require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{path}は配列である必要があります")
    return value


def require_string_list(value: Any, path: str, *, allow_empty: bool = True) -> list[str]:
    items = require_list(value, path)
    if not allow_empty and not items:
        raise ContractError(f"{path}には少なくとも1件必要です")
    for index, item in enumerate(items):
        require_string(item, f"{path}[{index}]")
    return items


def require_schema_version(value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise ContractError(f"{path}は整数の1である必要があります")


def ensure_unique(items: list[str], path: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        if item in seen and item not in duplicates:
            duplicates.append(item)
        seen.add(item)
    if duplicates:
        raise ContractError(f"{path}に重複IDがあります: {', '.join(duplicates)}")


def require_field(data: dict[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise ContractError(f"{path}.{key}がありません")
    return data[key]


def validate_catalog(value: Any) -> dict[str, list[str]]:
    catalog = require_mapping(value, "catalog")
    require_schema_version(require_field(catalog, "schema_version", "catalog"), "catalog.schema_version")

    principles = require_string_list(require_field(catalog, "principles", "catalog"), "catalog.principles")
    expected_principles = [str(number) for number in range(1, 41)]
    if set(principles) != set(expected_principles) or len(principles) != 40:
        raise ContractError("catalog.principlesにはID 1から40を各1件ずつ含める必要があります")
    ensure_unique(principles, "catalog.principles")

    standards = require_string_list(require_field(catalog, "standards", "catalog"), "catalog.standards")
    if len(standards) != 76:
        raise ContractError("catalog.standardsには階層IDが76件必要です")
    ensure_unique(standards, "catalog.standards")
    for index, standard in enumerate(standards):
        if not re.fullmatch(r"\d+(?:\.\d+)+", standard):
            raise ContractError(f"catalog.standards[{index}]は階層IDである必要があります: {standard}")

    ariz = require_string_list(require_field(catalog, "ariz", "catalog"), "catalog.ariz")
    expected_ariz = [str(number) for number in range(1, 10)]
    if set(ariz) != set(expected_ariz) or len(ariz) != 9:
        raise ContractError("catalog.arizにはID 1から9を各1件ずつ含める必要があります")
    ensure_unique(ariz, "catalog.ariz")

    tools = require_string_list(require_field(catalog, "tools", "catalog"), "catalog.tools", allow_empty=False)
    ensure_unique(tools, "catalog.tools")
    return {"principles": principles, "standards": standards, "ariz": ariz, "tools": tools}


def validate_problem(value: Any) -> None:
    problem = require_mapping(value, "review.problem")
    require_string(require_field(problem, "goal", "review.problem"), "review.problem.goal")
    for key in ("facts", "constraints", "unknowns"):
        require_string_list(require_field(problem, key, "review.problem"), f"review.problem.{key}")


def validate_common_row(value: Any, path: str, decisions: set[str]) -> tuple[dict[str, Any], str]:
    row = require_mapping(value, path)
    identifier = require_string(require_field(row, "id", path), f"{path}.id")
    decision = require_string(require_field(row, "decision", path), f"{path}.decision")
    if decision not in decisions:
        raise ContractError(f"{path}.decisionは許可されていません: {decision}")
    for key in ("reason", "source", "evidence"):
        require_string(require_field(row, key, path), f"{path}.{key}")
    return row, identifier


def validate_used_tool_searches(row: dict[str, Any], path: str, identifier: str) -> None:
    """指定した道具に検索記録があるか確認する。

    記録の構造だけを調べ、検索・結果・移植案の真偽は判定しない。
    """
    if identifier not in SEARCH_RECORD_REQUIRED_TOOL_IDS or row["decision"] != "used":
        return

    searches = require_list(require_field(row, "searches", path), f"{path}.searches")
    if not searches:
        raise ContractError(
            f"{path}.searchesには、実行した検索を少なくとも1件記録する必要があります"
        )
    for index, raw_search in enumerate(searches):
        search_path = f"{path}.searches[{index}]"
        search = require_mapping(raw_search, search_path)
        for key in ("provider", "query", "source_url", "domain", "adaptation"):
            require_string(require_field(search, key, search_path), f"{search_path}.{key}")
        results = require_list(require_field(search, "results", search_path), f"{search_path}.results")
        for result_index, raw_result in enumerate(results):
            result_path = f"{search_path}.results[{result_index}]"
            result = require_mapping(raw_result, result_path)
            require_string(require_field(result, "name", result_path), f"{result_path}.name")
            require_string(require_field(result, "url", result_path), f"{result_path}.url")


def validate_section(
    value: Any,
    name: str,
    expected_ids: list[str],
    decisions: set[str],
    candidate_references: set[str],
) -> list[str]:
    rows = require_list(value, f"review.{name}")
    identifiers: list[str] = []
    expected = set(expected_ids)
    for index, raw_row in enumerate(rows):
        path = f"review.{name}[{index}]"
        row, identifier = validate_common_row(raw_row, path, decisions)
        if identifier not in expected:
            raise ContractError(f"{path}.idがcatalog.{name}にありません: {identifier}")
        identifiers.append(identifier)
        if name == "tools":
            validate_used_tool_searches(row, path, identifier)
        if name in {"principles", "standards"} and row["decision"] == "candidate":
            candidate_ids = require_string_list(
                require_field(row, "candidate_ids", path),
                f"{path}.candidate_ids",
                allow_empty=False,
            )
            ensure_unique(candidate_ids, f"{path}.candidate_ids")
            candidate_references.update(candidate_ids)
    ensure_unique(identifiers, f"review.{name}")
    return identifiers


def validate_effects(value: Any) -> None:
    effects = require_mapping(value, "review.effects")
    queries = require_list(require_field(effects, "queries", "review.effects"), "review.effects.queries")
    reason = require_string(
        require_field(effects, "reason", "review.effects"),
        "review.effects.reason",
        allow_blank=True,
    )
    has_result = False
    for index, query in enumerate(queries):
        path = f"review.effects.queries[{index}]"
        item = require_mapping(query, path)
        for key in ("provider", "query", "source_url"):
            require_string(require_field(item, key, path), f"{path}.{key}")
        results = require_list(require_field(item, "results", path), f"{path}.results")
        for result_index, result in enumerate(results):
            result_path = f"{path}.results[{result_index}]"
            result_item = require_mapping(result, result_path)
            require_string(require_field(result_item, "name", result_path), f"{result_path}.name")
            require_string(require_field(result_item, "url", result_path), f"{result_path}.url")
            has_result = True
    if not has_result and not reason.strip():
        raise ContractError("review.effectsには実際の結果または空でないreasonが必要です")


def validate_candidates(value: Any) -> set[str]:
    candidates = require_list(value, "review.candidates")
    identifiers: list[str] = []
    for index, raw_candidate in enumerate(candidates):
        path = f"review.candidates[{index}]"
        candidate = require_mapping(raw_candidate, path)
        identifier = require_string(require_field(candidate, "id", path), f"{path}.id")
        identifiers.append(identifier)
        require_string(require_field(candidate, "mechanism", path), f"{path}.mechanism")
        require_string_list(require_field(candidate, "constraints", path), f"{path}.constraints", allow_empty=False)
        require_string_list(require_field(candidate, "failure_modes", path), f"{path}.failure_modes", allow_empty=False)
        require_string(require_field(candidate, "test", path), f"{path}.test")
        evidence_level = require_string(require_field(candidate, "evidence_level", path), f"{path}.evidence_level")
        if evidence_level not in {"hypothesis", "source_supported", "tested"}:
            raise ContractError(f"{path}.evidence_levelは許可されていません: {evidence_level}")
    ensure_unique(identifiers, "review.candidates")
    return set(identifiers)


def validate_review(value: Any, catalog: dict[str, list[str]]) -> str:
    review = require_mapping(value, "review")
    require_schema_version(require_field(review, "schema_version", "review"), "review.schema_version")
    status = require_string(require_field(review, "status", "review"), "review.status")
    if status not in {"complete", "incomplete"}:
        raise ContractError("review.statusはcompleteまたはincompleteである必要があります")
    validate_problem(require_field(review, "problem", "review"))

    candidate_references: set[str] = set()
    decisions = {
        "tools": {"used", "not_applicable", "blocked"},
        "principles": {"candidate", "not_applicable", "blocked"},
        "standards": {"candidate", "not_applicable", "blocked"},
        "ariz": {"performed", "not_applicable", "blocked"},
    }
    section_ids: dict[str, list[str]] = {}
    for name in ("tools", "principles", "standards", "ariz"):
        section_ids[name] = validate_section(
            require_field(review, name, "review"),
            name,
            catalog[name],
            decisions[name],
            candidate_references,
        )

    validate_effects(require_field(review, "effects", "review"))
    candidate_ids = validate_candidates(require_field(review, "candidates", "review"))
    missing_candidates = sorted(candidate_references - candidate_ids)
    if missing_candidates:
        raise ContractError(f"原理/標準のcandidate_idsがreview.candidatesにありません: {', '.join(missing_candidates)}")

    if status == "complete":
        for name in ("tools", "principles", "standards", "ariz"):
            if set(section_ids[name]) != set(catalog[name]) or len(section_ids[name]) != len(catalog[name]):
                raise ContractError(f"completeレビューのreview.{name}のID集合がcatalog.{name}と異なります")
        for name in ("tools", "principles", "standards", "ariz"):
            rows = require_list(review[name], f"review.{name}")
            if any(row["decision"] == "blocked" for row in rows):
                raise ContractError(f"completeレビューのreview.{name}にblockedは含められません")
    return status


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="フルTRIZレビューJSONの構造を検証します。")
    parser.add_argument("review", type=Path, help="検証するREVIEW.json")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "references" / "triz-catalog.json",
        help="カタログJSONのパス（既定値: ../references/triz-catalog.json）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        catalog = validate_catalog(load_json(args.catalog, "catalog"))
        status = validate_review(load_json(args.review, "review"), catalog)
    except ContractError as error:
        print(f"構造検証失敗: {error}", file=sys.stderr)
        return 1

    print("構造検証のみ。内容の妥当性は未判定")
    if status == "complete":
        print("完了: カタログID・必須項目・候補参照の構造を検証しました")
        return 0
    print("未完了: 情報不足または未実行の検討があります")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
