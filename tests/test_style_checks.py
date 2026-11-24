# -*- coding: utf-8 -*-
"""신규 문체·서식·맞춤법·과장·물결표 점검 (core/rules.py) 긍정·부정 테스트."""

import pytest

from core.rules import (
    RULE_PATTERNS,
    RULES_BASIS_LABEL,
    check_spelling,
    collect_style_issues,
    fix_tilde_ranges,
    rule_based_filter,
    style_check,
)


def _has(warnings, keyword):
    return any(keyword in w for w in warnings)


# ── 기준 학년도·근거 쪽수 ──
def test_rules_basis_label():
    assert RULES_BASIS_LABEL == "2026학년도 기재요령(교육부훈령 제555호) 기준"


def test_rule_patterns_have_rule_id_and_page_basis():
    for pattern, reason, basis, rule_id in RULE_PATTERNS:
        assert rule_id
        if rule_id not in ("youth_org", "gifted_edu"):  # 쪽수 미확인 → 조항명만
            assert "p.18-19" in basis or "p.118·149" in basis, basis


def test_rule_based_filter_carries_rule_id():
    f = rule_based_filter("TOEIC 시험을 준비함")
    assert any(x["rule_id"] == "language_test" for x in f)
    custom = rule_based_filter("청람제 준비에 참여함", ["청람제"])
    assert any(x["rule_id"] == "custom_word" for x in custom)


# ── 허위·과장 표현 ──
@pytest.mark.parametrize(
    "text",
    [
        "학급 최고의 발표를 선보임",
        "실험을 완벽하게 수행함",
        "독보적인 탐구 역량을 보임",
        "압도적인 성과를 거둠",
        "누구보다 성실하게 탐구함",
        "천재적인 직관을 보임",
        "타의 추종을 불허하는 실력을 보임",
    ],
)
def test_exaggeration_flagged(text):
    assert _has(style_check(text), "허위·과장")


@pytest.mark.parametrize(
    "text",
    [
        "다항식의 최고차항 계수를 비교함",
        "하루 최고 기온 자료를 분석함",
        "완벽주의 성향을 돌아보며 계획을 조정함",
        "천재지변에 대비하는 방법을 조사함",
        "누구나 이해할 수 있도록 설명함",
    ],
)
def test_exaggeration_not_flagged(text):
    assert not _has(style_check(text), "허위·과장")


# ── 물결표: 숫자 범위여도 항상 경고 + 붙임표 안내 ──
@pytest.mark.parametrize(
    "text", ["매주 3~4시간 탐구함", "2026.3.~7. 동안 기록함", "가~나 단원을 정리함",
             "3∼4시간", "3〜4시간", "3～4시간"]
)
def test_tilde_always_warned_with_hyphen_guide(text):
    ws = style_check(text)
    assert _has(ws, "특수문자")
    assert _has(ws, "붙임표 '-'")


def test_hyphen_range_not_warned():
    assert not _has(style_check("매주 3-4시간 탐구함"), "특수문자")


def test_tilde_issue_has_replacement_suggestion():
    issues = [i for i in collect_style_issues("매주 3~4시간 탐구함") if i["rule_id"] == "tilde"]
    assert issues and issues[0]["suggestion"] == "3~4시간 → 3-4시간"


@pytest.mark.parametrize(
    "src, expected",
    [
        ("3~4시간", "3-4시간"),
        ("2026.3.~7.", "2026.3.-7."),
        ("3 ～ 4", "3-4"),
        ("1∼2학년", "1-2학년"),
        ("가~나", "가~나"),
        ("~를 위해", "~를 위해"),
    ],
)
def test_fix_tilde_ranges(src, expected):
    assert fix_tilde_ranges(src) == expected


# ── 서식 점검 ──
def test_double_space_flagged():
    ws = style_check("미래 진로를  구체적으로 구상함.")
    assert _has(ws, "연속 공백")


def test_single_space_and_indent_not_flagged():
    assert not _has(style_check("  미래 진로를 구체적으로 구상함."), "연속 공백")


def test_space_before_punct_flagged():
    assert _has(style_check("자료를 정리함 . 발표함."), "문장부호 앞 공백")


def test_space_before_punct_not_flagged_for_ellipsis_or_decimal():
    ws = style_check("값은 .5 정도임. 그리고 ... 정리함.")
    assert not _has(ws, "문장부호 앞 공백")


def test_missing_space_after_period_flagged():
    assert _has(style_check("자료를 정리함.발표함."), "마침표 뒤 공백 누락")


@pytest.mark.parametrize("text", ["평균 3.5점 차이를 분석함.", "2026.3.4. 실험함.", "U.S. 자료를 읽음."])
def test_missing_space_after_period_exceptions(text):
    assert not _has(style_check(text), "마침표 뒤 공백 누락")


def test_double_punct_flagged():
    assert _has(style_check("자료를 정리함.. 발표함."), "중복 문장부호")
    assert _has(style_check("자료,, 그래프를 정리함."), "중복 문장부호")


def test_ellipsis_not_double_punct():
    assert not _has(style_check("그리고... 정리함. 또…… 발표함. 끝...... 정리함."), "중복 문장부호")


def test_missing_final_period_flagged_when_other_sentences_have_period():
    ws = style_check("자료를 수집함. 결과를 발표함")
    assert _has(ws, "문장 끝 마침표 누락")


def test_missing_final_period_not_flagged_without_any_period():
    # 마침표를 전혀 쓰지 않은 한 줄 메모·짧은 예시는 잡지 않는다.
    assert style_check("데이터를 수집하여 그래프로 표현하고 원인을 고찰함") == []


def test_missing_final_period_not_flagged_for_wrapped_line():
    text = "교육 분야와 연계하여\n심도 있게 탐구함."
    assert not _has(style_check(text), "문장 끝 마침표 누락")


# ── 맞춤법 로컬 규칙 ──
def _fixes(text):
    return [(i["word"], i["suggestion"]) for i in check_spelling(text)]


@pytest.mark.parametrize(
    "text, wrong, fix",
    [
        ("몇일 동안 기록함", "몇일", "며칠"),
        ("실험이 잘 됬음", "됬", "됐"),
        ("그렇게 하면 안되.", "안되", "안 돼/안돼"),
        ("발표 전 설레임을 느낌", "설레임", "설렘"),
        ("금새 이해함", "금새", "금세"),
        ("웬지 궁금해짐", "웬지", "왠지"),
        ("어떻해 해결할지 고민함", "어떻해", "어떻게/어떡해"),
        ("심도있게 탐구함", "심도있", "심도 있"),
        ("의미있는 결과를 얻음", "의미있", "의미 있"),
        ("오류인 것같음", "것같", "것 같"),
        ("시험 합격율을 분석함", "격율", "격률"),
        ("실험 실패률을 계산함", "패률", "패율"),
        ("백분률로 나타냄", "분률", "분율"),
        ("해결할수있음", "할수있", "할 수 있"),
        ("해결할 수있음", "할 수있", "할 수 있"),
        ("포기할수밖에 없었음", "할수밖에", "할 수밖에"),
        ("궁금한것은 질문함", "한것", "한 것"),
        ("배운것을 정리함", "운것", "운 것"),
    ],
)
def test_spelling_flagged(text, wrong, fix):
    assert (wrong, fix) in _fixes(text), _fixes(text)


@pytest.mark.parametrize(
    "text",
    [
        "며칠 동안 기록함",
        "실험이 잘 됐음",
        "안 되는 이유를 분석함",
        "안되는 이유를 분석함",
        "설렘을 느낌",
        "금세 이해함",
        "물건의 금새를 비교함",
        "왠지 궁금해짐",
        "웬 사람이 찾아옴",
        "어떻게 해결할지 고민함",
        "어떡해야 할지 고민함",
        "심도 있게 탐구함",
        "재미있는 실험을 설계함",
        "맛있는 급식 메뉴를 조사함",
        "오류인 것 같음",
        "합격률과 백분율, 실패율, 출석률을 비교함",
        "비율과 규율, 자율, 효율, 선율, 법률, 확률, 환율을 정리함",
        "김율 학생과 협업함",
        "해결할 수 있음",
        "갈수록 실력이 늘어남",
        "수없이 반복해 연습함",
        "실험을 수없이 반복함",
        "궁금한 것은 질문함",
        "날것 그대로의 자료를 살핌",
        "들것을 이용한 응급 처치를 익힘",
        "그것을 정리함",
        "때문에 결과가 달라짐",
    ],
)
def test_spelling_not_flagged(text):
    assert _fixes(text) == []


def test_spelling_warning_shows_basis():
    ws = style_check("몇일 동안 기록함.")
    assert _has(ws, "맞춤법 점검(로컬 규칙)")
    assert _has(ws, "한글 맞춤법 제27항")


# ── collect_style_issues 구조 ──
def test_collect_style_issues_has_spans():
    text = "궁금한것은 질문함. 미래 진로를  구체적으로 구상함."
    issues = collect_style_issues(text)
    assert issues
    for i in issues:
        assert {"rule_id", "category", "start", "end", "word", "problem", "suggestion"} <= set(i)
        assert 0 <= i["start"] <= i["end"] <= len(text)
    geot = next(i for i in issues if i["word"] == "한것")
    assert text[geot["start"]:geot["end"]] == "한것"


def test_style_check_is_string_view_of_same_checks():
    text = "실험을 진행하였다. 몇일 동안 3~4시간 탐구함"
    ws = style_check(text)
    assert _has(ws, "개조식")
    assert _has(ws, "맞춤법")
    assert _has(ws, "특수문자")
    assert all(isinstance(w, str) for w in ws)


# ── 예시 세특(사용자 제공 형태) 회귀: 실제 오타는 잡고 평범한 문장은 오탐 없음 ──
USER_EXAMPLE = (
    "(1학기)정보: 수업시간에 궁금한것은 질문하며 문제를 창의적인 방법으로 접근함. "
    "'디지털 기술에 따른 사회 변화 조사 보고서' 작성 활동에서 교육 분야와 연계하여 "
    "심도있게 탐구하고 미래 진로를  구체적으로 구상함."
)


def test_user_example_typos_caught():
    ws = style_check(USER_EXAMPLE)
    joined = " ".join(ws)
    assert "「한것」" in joined
    assert "「심도있」" in joined
    assert _has(ws, "연속 공백")


def test_six_subject_example_has_no_new_rule_warnings():
    from tests.test_parsing import SIX_SUBJECT_TEXT

    ws = style_check(SIX_SUBJECT_TEXT)
    for kw in ("허위·과장", "서식 점검", "맞춤법 점검", "특수문자"):
        assert not _has(ws, kw), ws
