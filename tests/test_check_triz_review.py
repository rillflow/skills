from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "skills" / "ideation"
CHECKER = ROOT / "scripts" / "check_triz_review.py"


def catalog() -> dict:
    return {
        "schema_version": 1,
        "principles": [str(number) for number in range(1, 41)],
        "standards": [f"1.1.{number}" for number in range(1, 77)],
        "ariz": [str(number) for number in range(1, 10)],
        "tools": [
            "function_analysis",
            "function-oriented-search",
            "analogue-problems",
            "ceca",
            "effects_search",
        ],
    }


def row(identifier: str, decision: str) -> dict:
    return {
        "id": identifier,
        "decision": decision,
        "reason": "fixture reason",
        "source": "fixture source",
        "evidence": "fixture evidence",
    }


def search_record(*, query: str = "fixture query", results: list[dict] | None = None) -> dict:
    return {
        "provider": "fixture provider",
        "query": query,
        "source_url": "https://example.test/search",
        "domain": "fixture domain",
        "adaptation": "fixture adaptation",
        "results": [] if results is None else results,
    }


def tool_row(identifier: str, decision: str) -> dict:
    item = row(identifier, decision)
    if identifier in {"function-oriented-search", "analogue-problems"} and decision == "used":
        item["searches"] = [search_record()]
    return item


def complete_review() -> dict:
    data = catalog()
    return {
        "schema_version": 1,
        "status": "complete",
        "problem": {
            "goal": "fixture goal",
            "facts": ["fixture fact"],
            "constraints": ["fixture constraint"],
            "unknowns": [],
        },
        "tools": [tool_row(identifier, "used") for identifier in data["tools"]],
        "principles": [row(identifier, "not_applicable") for identifier in data["principles"]],
        "standards": [row(identifier, "not_applicable") for identifier in data["standards"]],
        "ariz": [row(identifier, "performed") for identifier in data["ariz"]],
        "effects": {"queries": [], "reason": "No external effect was needed for this fixture."},
        "candidates": [],
    }


class CheckTrizReviewTests(unittest.TestCase):
    def run_checker(self, review: dict | None, *, raw_review: str | None = None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            catalog_path = directory / "catalog.json"
            review_path = directory / "review.json"
            catalog_path.write_text(json.dumps(catalog()), encoding="utf-8")
            if raw_review is not None:
                review_path.write_text(raw_review, encoding="utf-8")
            else:
                review_path.write_text(json.dumps(review), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(CHECKER), str(review_path), "--catalog", str(catalog_path)],
                text=True,
                capture_output=True,
                check=False,
            )

    def test_complete_review_passes_with_structural_only_notice(self) -> None:
        result = self.run_checker(complete_review())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("構造検証のみ。内容の妥当性は未判定", result.stdout)

    def test_complete_review_fails_when_a_principle_is_missing(self) -> None:
        review = complete_review()
        review["principles"].pop()
        result = self.run_checker(review)
        self.assertEqual(result.returncode, 1)
        self.assertIn("review.principles", result.stderr)

    def test_duplicate_standard_fails(self) -> None:
        review = complete_review()
        review["standards"][-1]["id"] = review["standards"][0]["id"]
        result = self.run_checker(review)
        self.assertEqual(result.returncode, 1)
        self.assertIn("重複ID", result.stderr)

    def test_missing_candidate_reference_fails(self) -> None:
        review = complete_review()
        review["principles"][0]["decision"] = "candidate"
        review["principles"][0]["candidate_ids"] = ["candidate-does-not-exist"]
        result = self.run_checker(review)
        self.assertEqual(result.returncode, 1)
        self.assertIn("candidate_ids", result.stderr)

    def test_complete_review_cannot_have_blocked_row(self) -> None:
        review = complete_review()
        review["tools"][0]["decision"] = "blocked"
        result = self.run_checker(review)
        self.assertEqual(result.returncode, 1)
        self.assertIn("blocked", result.stderr)

    def test_function_oriented_search_used_requires_nonempty_searches(self) -> None:
        for mutation in ("missing", "empty"):
            with self.subTest(mutation=mutation):
                review = complete_review()
                for item in review["tools"]:
                    if item["id"] == "function-oriented-search":
                        if mutation == "missing":
                            item.pop("searches")
                        else:
                            item["searches"] = []
                result = self.run_checker(review)
                self.assertEqual(result.returncode, 1)
                self.assertIn("searches", result.stderr)

    def test_function_oriented_search_used_rejects_blank_query(self) -> None:
        review = complete_review()
        for item in review["tools"]:
            if item["id"] == "function-oriented-search":
                item["searches"][0]["query"] = " "
        result = self.run_checker(review)
        self.assertEqual(result.returncode, 1)
        self.assertIn("query", result.stderr)

    def test_function_oriented_search_used_with_empty_results_passes(self) -> None:
        result = self.run_checker(complete_review())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_search_required_tools_not_applicable_do_not_require_searches(self) -> None:
        review = complete_review()
        for item in review["tools"]:
            if item["id"] in {"function-oriented-search", "analogue-problems"}:
                item["decision"] = "not_applicable"
                item.pop("searches")
        result = self.run_checker(review)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_analogue_problems_used_requires_valid_search(self) -> None:
        for mutation, expected in (
            ("missing", "searches"),
            ("empty", "searches"),
            ("blank_query", "query"),
        ):
            with self.subTest(mutation=mutation):
                review = complete_review()
                for item in review["tools"]:
                    if item["id"] == "analogue-problems":
                        if mutation == "missing":
                            item.pop("searches")
                        elif mutation == "empty":
                            item["searches"] = []
                        else:
                            item["searches"][0]["query"] = " "
                result = self.run_checker(review)
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)

    def test_complete_review_fails_when_a_tool_is_missing(self) -> None:
        review = complete_review()
        review["tools"].pop()
        result = self.run_checker(review)
        self.assertEqual(result.returncode, 1)
        self.assertIn("review.tools", result.stderr)

    def test_blank_required_body_fails(self) -> None:
        review = complete_review()
        review["principles"][0]["reason"] = " "
        result = self.run_checker(review)
        self.assertEqual(result.returncode, 1)
        self.assertIn("空にできません", result.stderr)

    def test_incomplete_review_allows_empty_sections_and_exits_two(self) -> None:
        review = complete_review()
        review["status"] = "incomplete"
        review["problem"]["unknowns"] = ["問題モデルを作る事実がない"]
        for section in ("tools", "principles", "standards", "ariz"):
            review[section] = []
        result = self.run_checker(review)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("未完了", result.stdout)

    def test_malformed_json_and_null_field_fail(self) -> None:
        malformed = self.run_checker(None, raw_review="{\"schema_version\": 1,")
        self.assertEqual(malformed.returncode, 1)
        review = complete_review()
        review["problem"]["facts"] = None
        null_value = self.run_checker(review)
        self.assertEqual(null_value.returncode, 1)

    def test_duplicate_json_key_fails(self) -> None:
        result = self.run_checker(
            None,
            raw_review='{"schema_version": 1, "schema_version": 1}',
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("JSONキーが重複", result.stderr)

    def test_os_error_fails_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            catalog_path = directory / "catalog.json"
            catalog_path.write_text(json.dumps(catalog()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(CHECKER), str(directory), "--catalog", str(catalog_path)],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("読み込めません", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
