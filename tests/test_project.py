# -*- coding: utf-8 -*-
from core.project import replace_in_entries


# ── 텍스트 일괄 치환 ──
def test_replace_counts_multiple_occurrences_across_entries():
    entries = [
        {"name": "A", "draft": "탐구 탐구 탐구"},
        {"name": "B", "draft": "탐구 활동"},
    ]
    new, n = replace_in_entries(entries, "draft", "탐구", "연구")
    assert n == 4
    assert new[0]["draft"] == "연구 연구 연구"
    assert new[1]["draft"] == "연구 활동"


def test_replace_is_non_mutating():
    entries = [{"name": "A", "draft": "탐구"}]
    new, n = replace_in_entries(entries, "draft", "탐구", "연구")
    # 원본은 변하지 않는다.
    assert entries[0]["draft"] == "탐구"
    assert new[0]["draft"] == "연구"
    assert new[0] is not entries[0]


def test_replace_empty_find_returns_zero_and_unchanged():
    entries = [{"name": "A", "draft": "탐구"}]
    new, n = replace_in_entries(entries, "draft", "", "연구")
    assert n == 0
    assert new[0]["draft"] == "탐구"


def test_replace_skips_entries_missing_field():
    entries = [{"name": "A"}, {"name": "B", "draft": "탐구"}]
    new, n = replace_in_entries(entries, "draft", "탐구", "연구")
    assert n == 1
    assert "draft" not in new[0]
    assert new[1]["draft"] == "연구"
