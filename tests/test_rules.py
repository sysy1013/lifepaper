# -*- coding: utf-8 -*-
from core.rules import (
    filter_ignored,
    find_similar_pairs,
    neis_bytes,
    parse_custom_words,
    rule_based_filter,
    style_check,
)


# ── neis_bytes ──
def test_neis_bytes_pure_ascii_counts_one_each():
    assert neis_bytes("abc 123") == 7


def test_neis_bytes_hangul_counts_three_each():
    assert neis_bytes("가나다") == 9


def test_neis_bytes_mixed_string_exact_count():
    # "홍길동A1" → 한글 3자×3 + ASCII 2자×1 = 11
    assert neis_bytes("홍길동A1") == 11


def test_neis_bytes_empty_is_zero():
    assert neis_bytes("") == 0


# ── rule_based_filter ──
def test_rule_filter_detects_language_score():
    findings = rule_based_filter("교내 활동 중 TOEIC 900점을 취득함")
    words = [f["word"] for f in findings]
    assert any("TOEIC" in w for w in words)
    assert any("900" in w for w in words)


def test_rule_filter_detects_mock_exam_grade():
    findings = rule_based_filter("모의고사 1등급을 유지함")
    assert findings
    words = " ".join(f["word"] for f in findings)
    assert "모의고사" in words or "1등급" in words


def test_rule_filter_detects_custom_word():
    # 내장 패턴에 걸리지 않는 중립 단어를 사용자 정의 금칙어로 등록한다.
    findings = rule_based_filter("우리 반딧불 동아리 활동임", custom_words=["반딧불"])
    match = [f for f in findings if f["word"] == "반딧불"]
    assert len(match) == 1
    assert match[0]["reason"] == "사용자 정의 금칙어"


def test_rule_filter_clean_text_returns_empty():
    assert rule_based_filter("탐구 활동을 통해 꾸준히 성장하는 모습을 보임") == []


def test_parse_custom_words_splits_on_comma_and_newline():
    assert parse_custom_words("홍길동, 20301\n○○학원") == ["홍길동", "20301", "○○학원"]
    assert parse_custom_words("  ,\n  ") == []


# ── style_check ──
def test_style_check_flags_non_gaejosik_endings():
    warnings = style_check("실험을 진행하였다. 결과를 분석하였다. 보고서를 작성하였다.")
    assert any("개조식" in w for w in warnings)


def test_style_check_flags_repeated_endings():
    # 같은 종결 어미('함.')가 6문장에서 반복 → 반복 경고
    warnings = style_check("탐구함. 발표함. 정리함. 분석함. 성찰함. 기록함.")
    assert any("반복" in w for w in warnings)


def test_style_check_flags_cliche():
    warnings = style_check("수업에 적극적으로 참여함")
    assert any("상투적" in w for w in warnings)


def test_style_check_clean_text_returns_empty():
    assert style_check("데이터를 수집하여 그래프로 표현하고 원인을 고찰함") == []


# ── find_similar_pairs ──
def test_find_similar_pairs_flags_near_identical():
    items = [
        ("학생A", "탐구 과정을 통해 데이터를 분석하고 결론을 도출함"),
        ("학생B", "탐구 과정을 통해 데이터를 분석하고 결론을 도출함"),
        ("학생C", "전혀 다른 주제로 봉사활동에 참여한 경험을 기록함"),
    ]
    pairs = find_similar_pairs(items)
    assert len(pairs) == 1
    assert {pairs[0][0], pairs[0][1]} == {"학생A", "학생B"}
    assert pairs[0][2] >= 0.55


# ── filter_ignored ──
def test_filter_ignored_removes_matching_words():
    findings = [{"word": "TOEIC"}, {"word": "김철수"}, {"word": "서울대"}]
    result = filter_ignored(findings, {"김철수"})
    words = [f["word"] for f in result]
    assert "김철수" not in words
    assert words == ["TOEIC", "서울대"]


def test_filter_ignored_empty_is_noop():
    findings = [{"word": "TOEIC"}, {"word": "서울대"}]
    assert filter_ignored(findings, []) == findings
