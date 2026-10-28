#!/usr/bin/env python3
"""Audit unlimited exhaustive discovery or a finite selected-notice review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


FINAL_CLASSIFICATIONS = {"ranked", "rejected", "blocked"}


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def audit(data: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    mode = data.get("mode", "exhaustive")
    require(mode in {"exhaustive", "shortlist_review"}, "invalid audit mode", errors)
    finite_review = mode == "shortlist_review"
    expected = data.get("expected_search_ids", [] if finite_review else None)
    searches = data.get("searches", [] if finite_review else None)
    notices = data.get("notices")

    require(isinstance(expected, list) and (bool(expected) or finite_review), "expected_search_ids must be a non-empty list for discovery", errors)
    require(isinstance(searches, list), "searches must be a list", errors)
    require(isinstance(notices, list), "notices must be a list", errors)
    if errors:
        return {"ok": False, "errors": errors}

    expected_ids = [str(value) for value in expected]
    require(len(expected_ids) == len(set(expected_ids)), "expected_search_ids contains duplicates", errors)
    selected = data.get("selected_notice_numbers", [])
    if finite_review:
        require(isinstance(selected, list) and bool(selected), "selected_notice_numbers must be a non-empty list", errors)
        require(not expected and not searches, "finite review must not invent search queries", errors)
        if isinstance(selected, list):
            require(len(selected) == len(set(map(str, selected))), "duplicate selected notice numbers", errors)

    search_by_id: dict[str, dict[str, Any]] = {}
    discovered: set[str] = set()
    pages_visited = 0
    for index, search in enumerate(searches):
        require(isinstance(search, dict), f"searches[{index}] must be an object", errors)
        if not isinstance(search, dict):
            continue
        search_id = str(search.get("id", ""))
        require(bool(search_id), f"searches[{index}].id is required", errors)
        require(search_id not in search_by_id, f"duplicate search id: {search_id}", errors)
        search_by_id[search_id] = search
        required = search.get("required", True)
        if required:
            require(search.get("pagination_complete") is True, f"search {search_id}: pagination is incomplete", errors)
            page_count = search.get("pages_visited")
            require(isinstance(page_count, int) and page_count >= 1, f"search {search_id}: pages_visited must be >= 1", errors)
            if isinstance(page_count, int):
                pages_visited += page_count
        numbers = search.get("discovered_notice_numbers", [])
        require(isinstance(numbers, list), f"search {search_id}: discovered_notice_numbers must be a list", errors)
        if isinstance(numbers, list):
            discovered.update(str(value) for value in numbers if str(value))

    missing_searches = sorted(set(expected_ids) - set(search_by_id))
    unexpected_missing = ", ".join(missing_searches)
    require(not missing_searches, f"expected searches not executed: {unexpected_missing}", errors)
    partial_searches = sorted(search_id for search_id in expected_ids if search_id in search_by_id and search_by_id[search_id].get("pagination_complete") is not True)
    coverage_blockers = data.get("coverage_blockers", [])
    require(isinstance(coverage_blockers, list), "coverage_blockers must be a list", errors)
    if mode == "exhaustive":
        require(not coverage_blockers, "exhaustive search has coverage blockers", errors)

    notice_by_number: dict[str, dict[str, Any]] = {}
    analyzed = rejected = blocked = irrelevant = 0
    for index, notice in enumerate(notices):
        require(isinstance(notice, dict), f"notices[{index}] must be an object", errors)
        if not isinstance(notice, dict):
            continue
        number = str(notice.get("notice_number", ""))
        require(bool(number), f"notices[{index}].notice_number is required", errors)
        require(number not in notice_by_number, f"duplicate notice ledger entry: {number}", errors)
        notice_by_number[number] = notice
        triage = notice.get("triage")
        require(triage in {"irrelevant", "analyze"}, f"notice {number}: triage must be irrelevant or analyze", errors)
        if triage == "irrelevant":
            irrelevant += 1
            require(bool(notice.get("reason")), f"notice {number}: irrelevant decision needs a reason", errors)
            continue
        if triage != "analyze":
            continue
        analyzed += 1
        require(notice.get("card_opened") is True, f"notice {number}: card was not opened", errors)
        require(notice.get("status_checked") is True, f"notice {number}: submission status was not checked", errors)
        require(notice.get("document_review") in {"complete", "partial", "unavailable"}, f"notice {number}: document_review is invalid", errors)
        classification = notice.get("classification")
        require(classification in FINAL_CLASSIFICATIONS, f"notice {number}: final classification is missing", errors)
        require(notice.get("report_included") is True, f"notice {number}: analyzed notice is absent from report", errors)
        rejected += classification == "rejected"
        blocked += classification == "blocked"

    missing_notices = sorted(discovered - set(notice_by_number))
    require(not missing_notices, "discovered notices missing from ledger: " + ", ".join(missing_notices), errors)
    if finite_review and isinstance(selected, list):
        selected_set = set(map(str, selected))
        require(selected_set == set(notice_by_number), "finite review ledger must cover exactly selected_notice_numbers", errors)
        require(all(n.get("triage") == "analyze" for n in notice_by_number.values()), "selected notices require document review and disposition, not irrelevant-only triage", errors)
        pages_visited = sum(n.get("card_opened") is True for n in notice_by_number.values())

    summary = {
        "ok": not errors,
        "mode": mode,
        "coverage_complete": mode == "exhaustive" and not errors,
        "review_complete": finite_review and not errors,
        "expected_searches": len(expected_ids),
        "completed_searches": sum(
            1 for search_id in expected_ids
            if search_id in search_by_id and search_by_id[search_id].get("pagination_complete") is True
        ),
        "pages_visited": pages_visited,
        "raw_unique_notices": len(notice_by_number) if finite_review else len(discovered),
        "ledger_notices": len(notice_by_number),
        "analyzed": analyzed,
        "rejected": rejected,
        "blocked": blocked,
        "irrelevant": irrelevant,
        "unvisited_searches": missing_searches,
        "partial_searches": partial_searches,
        "coverage_blockers": coverage_blockers,
        "document_review_partial_or_unavailable": sum(n.get("triage") == "analyze" and n.get("document_review") != "complete" for n in notice_by_number.values()),
        "errors": errors,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, help="UTF-8 JSON search-run manifest")
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    result = audit(data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
