# -*- coding: utf-8 -*-
import json

from core.project import (
    PROJECT_VERSION,
    deserialize_project,
    replace_in_entries,
    serialize_project,
)


# ── 프로젝트 저장/복원 ──
def _sample_data():
    return {
        "batch_review": [
            {
                "name": "20301_홍길동",
                "text": "의사인 아버지의 영향을 받아 TOEIC 900점을 취득함.",
                "findings": [
                    {"word": "TOEIC", "reason": "공인어학시험", "severity": "위반"}
                ],
                "revised": "영어 역량을 길러 온 학생임.",
            }
        ],
        "history": [{"time": "10:00:00", "label": "검토", "content": "내용"}],
        "ignored_words": ["학교"],
        "mask_words_raw": "홍길동, 20301",
    }


def test_roundtrip_preserves_nested_dicts_and_korean():
    data = _sample_data()
    restored, err = deserialize_project(serialize_project(data))
    assert err == ""
    assert restored == data
    # 중첩 구조와 한글이 그대로 보존된다.
    assert restored["batch_review"][0]["findings"][0]["word"] == "TOEIC"
    assert restored["mask_words_raw"] == "홍길동, 20301"


def test_serialize_produces_valid_metadata():
    obj = json.loads(serialize_project({"history": []}))
    assert obj["app"] == "lifepaper"
    assert obj["version"] == PROJECT_VERSION
    assert "saved_at" in obj


def test_deserialize_only_restores_known_keys():
    raw = json.dumps(
        {
            "app": "lifepaper",
            "version": PROJECT_VERSION,
            "data": {"history": [], "unknown_key": 123},
        }
    )
    restored, err = deserialize_project(raw)
    assert err == ""
    assert "history" in restored
    assert "unknown_key" not in restored


def test_wrong_app_tag_returns_error():
    raw = json.dumps({"app": "other", "version": 1, "data": {}})
    restored, err = deserialize_project(raw)
    assert restored == {}
    assert err != ""


def test_future_version_returns_error():
    raw = json.dumps(
        {"app": "lifepaper", "version": PROJECT_VERSION + 1, "data": {"history": []}}
    )
    restored, err = deserialize_project(raw)
    assert restored == {}
    assert err != ""


def test_malformed_json_returns_error():
    restored, err = deserialize_project("{not json")
    assert restored == {}
    assert err != ""


def test_deserialize_accepts_bytes():
    raw = serialize_project({"history": []}).encode("utf-8")
    restored, err = deserialize_project(raw)
    assert err == ""
    assert restored == {"history": []}


def test_missing_data_dict_returns_error():
    raw = json.dumps({"app": "lifepaper", "version": 1, "data": "oops"})
    restored, err = deserialize_project(raw)
    assert restored == {}
    assert err != ""


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
