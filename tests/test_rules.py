# -*- coding: utf-8 -*-
import pytest

from core.rules import (
    filter_ignored,
    find_shared_sentences,
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


def test_rule_filter_detects_zoom_brand():
    findings = rule_based_filter("Zoom 수업에 참여함")
    words = [f["word"] for f in findings]
    assert any("Zoom" in w for w in words)


def test_rule_filter_does_not_flag_verb_ending_jum():
    text = (
        "탐구를 통해 물리 법칙이 실생활 문제 해결에 활용되는 "
        "의미를 이해하고자 하는 모습을 보여 줌."
    )
    findings = rule_based_filter(text)
    words = [f["word"] for f in findings]
    assert "줌" not in words
    assert findings == []


# '~해 주다'의 명사형 종결('~해 줌')은 개조식 세특에서 흔한 정상 표현이다.
# 띄어쓰기 유무와 선행 어간이 달라도 어떤 RULE_PATTERNS에도 걸리면 안 된다.
_JUM_VERB_ENDING_TEXTS = [
    "모둠원이 모은 실험 데이터를 표로 정리해 줌.",
    "친구가 놓친 부분을 짚어 발표 원고를 완성해 줌.",
    "마감 전에 보고서를 스스로 점검해 제출해 줌.",
    "개념을 어려워하는 친구를 끝까지 도와줌.",
    "풀이 과정을 단계별로 나누어 차근차근 알려 줌.",
    "직접 설계한 회로 모형을 모둠 전체가 쓰도록 만들어 줌.",
]


@pytest.mark.parametrize("text", _JUM_VERB_ENDING_TEXTS)
def test_rule_filter_does_not_flag_haejum_verb_endings(text):
    assert rule_based_filter(text) == [], f"'~해 줌' 종결 오탐: {text}"


def test_rule_filter_paragraph_with_multiple_jum_endings_is_clean():
    # 한 문단에 '줌' 종결이 여러 번 나와도 누적 오탐이 없어야 한다.
    text = (
        "실험 조건을 바꿔가며 데이터를 모으고 결과를 표로 정리해 줌. "
        "오차가 큰 구간을 찾아 원인을 설명해 줌. "
        "모둠원이 이해할 때까지 그래프 해석 방법을 알려 줌."
    )
    assert rule_based_filter(text) == []


def test_rule_filter_alone_misses_bare_korean_zoom_by_design():
    """규칙 단독으로는 한글 '줌' 브랜드 언급을 잡지 않는다 — 의도된 설계다.

    '줌 수업'(Zoom)과 '보여 줌'(주다의 명사형)을 정규식만으로 구분할 수 없어
    오탐 비용이 훨씬 컸다. 규칙 기반은 API 없이 도는 고속 보조 필터일 뿐이고,
    실제 1차 검출기는 항상 함께 실행되는 Gemini 심사다
    (core/gemini.py review_text가 rule_based_filter + analyze_with_gemini를
    병합하며, SYSTEM_PROMPT 심사기준 1번이 '줌'을 브랜드 예시로 명시한다).
    따라서 아래 결과는 버그가 아니라 합의된 트레이드오프이므로,
    한글 '줌' 패턴을 규칙에 되살리는 '수정'은 회귀다.
    """
    for text in ("줌 수업에 참여함", "줌으로 화상회의를 진행함"):
        words = [f["word"] for f in rule_based_filter(text)]
        assert not any("줌" in w for w in words), f"규칙이 한글 '줌'을 검출함: {text}"


def test_rule_filter_detects_zoom_embedded_in_korean_sentence():
    # 영문 'Zoom'은 한국어 조사가 바로 붙어도 여전히 검출되어야 한다 (미탐 방지).
    findings = rule_based_filter("Zoom으로 진행된 화상 수업에 참여함")
    assert any("Zoom" in f["word"] for f in findings)


@pytest.mark.parametrize("text", ["zoom 화상수업에 참여함", "ZOOM 회의에 참여함"])
def test_rule_filter_detects_zoom_case_insensitively(text):
    findings = rule_based_filter(text)
    assert any(f["word"].lower() == "zoom" for f in findings)


def test_rule_filter_clean_text_returns_empty():
    assert rule_based_filter("탐구 활동을 통해 꾸준히 성장하는 모습을 보임") == []


# ── rule_based_filter: 기재요령 엄격화 확장 패턴 ──
# (설명, 검출되어야 할 문장, 검출어에 포함되어야 할 조각)
_STRICT_POSITIVE_CASES = [
    ("기관명", "유네스코 세계유산 등재 기준을 조사함", "유네스코"),
    ("기관명(라틴 약어)", "OECD 통계 자료를 활용해 그래프를 그림", "OECD"),
    ("자격증", "컴퓨터활용능력 자격 취득을 준비함", "컴퓨터활용능력"),
    ("대회/수상", "수학경시대회에서 최우수상을 받음", "경시대회"),
    ("논문/출판", "탐구 결과를 학회지에 투고함", "학회지에"),
    ("지식재산권", "아이디어를 특허출원까지 진행함", "특허출원"),
    ("해외활동", "방학 중 해외 봉사 활동에 참여함", "해외 봉사"),
    ("장학금", "성적 우수 장학생으로 선발됨", "장학생으로 선발"),
    ("MOOC/방과후", "K-MOOC 강좌를 수강하며 개념을 보충함", "K-MOOC"),
    ("소논문", "소논문 형식으로 결과를 정리함", "소논문"),
    ("대학명 일반 패턴", "한국대학교 연구실을 견학함", "한국대학교"),
    ("브랜드 확장", "오픈AI의 도구로 자료를 정리함", "오픈AI"),
]


@pytest.mark.parametrize("label, text, fragment", _STRICT_POSITIVE_CASES)
def test_rule_filter_detects_strict_patterns(label, text, fragment):
    words = " ".join(f["word"] for f in rule_based_filter(text))
    assert fragment in words, f"{label} 패턴이 검출되지 않음: {text}"


# 위 패턴들이 평범한 세특 문장을 오탐하지 않는지 확인한다.
_STRICT_NEGATIVE_TEXTS = [
    "탐구 활동을 통해 꾸준히 성장하는 모습을 보임",
    "빛의 굴절 실험을 설계하고 오차 원인을 스스로 분석함",
    "모둠 활동에서 자료 조사를 맡아 근거를 정리하고 발표함",
    "진학할 대학교를 스스로 탐색하며 관심 분야를 넓힘",
    "지역 하천의 수질 자료를 수집해 그래프로 표현함",
]


@pytest.mark.parametrize("text", _STRICT_NEGATIVE_TEXTS)
def test_rule_filter_strict_patterns_no_false_positive(text):
    assert rule_based_filter(text) == []


def test_rule_filter_latin_acronyms_are_case_sensitive():
    # 'who', 'un' 같은 영어 단어가 WHO/UN 기관명으로 오탐되지 않아야 한다.
    assert rule_based_filter("the student who is unable to stop") == []


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


# ── style_check: 따옴표/괄호 짝 검사 ──
def test_style_check_flags_odd_single_quote():
    warnings = style_check("'인공지능의 윤리를 주제로 발표함")
    assert any("따옴표" in w for w in warnings)


def test_style_check_balanced_quotes_no_warning():
    warnings = style_check("'인공지능'의 윤리를 주제로 발표함")
    assert not any("따옴표" in w for w in warnings)


def test_style_check_flags_unmatched_open_bracket():
    warnings = style_check("자료(출처를 밝히며 정리함")
    assert any("괄호" in w for w in warnings)
    assert any("닫히지 않음" in w for w in warnings)


def test_style_check_flags_unmatched_close_bracket():
    warnings = style_check("자료 출처)를 밝히며 정리함")
    assert any("여는 괄호 없이" in w for w in warnings)


def test_style_check_balanced_brackets_no_warning():
    warnings = style_check("자료(출처)를 밝히며 정리함")
    assert not any("괄호" in w for w in warnings)


# ── style_check: 특수문자·문단구분기호 ──
def test_style_check_flags_special_characters():
    warnings = style_check("★ 핵심 개념을 스스로 정리함")
    assert any("특수문자" in w for w in warnings)


def test_style_check_flags_paragraph_numbering():
    warnings = style_check("1. 자료를 수집함\n2. 결과를 정리함")
    assert any("문단구분기호" in w for w in warnings)


# ── style_check: 한글 표기 원칙(영문 오남용) ──
def test_style_check_allowed_english_terms_no_warning():
    warnings = style_check("AI 도구와 PPT를 활용해 발표 자료를 만듦")
    assert not any("영문 표기" in w for w in warnings)


def test_style_check_flags_disallowed_english_word():
    warnings = style_check("Photoshop으로 포스터를 제작함")
    assert any("영문 표기" in w for w in warnings)
    assert any("Photoshop" in w for w in warnings)


# ── style_check: 신규 3개 검사의 오탐 회귀 가드 ──
CLEAN_SETEUK = (
    "환경 문제를 다룬 지문을 읽고 핵심 주장을 요약해 발표함. "
    "근거 문장을 스스로 골라 논리 구조를 설명하며 어휘의 쓰임을 정리함."
)


def test_style_check_normal_seteuk_paragraph_has_no_new_warnings():
    warnings = style_check(CLEAN_SETEUK)
    for keyword in ("따옴표", "괄호", "특수문자", "영문 표기"):
        assert not any(keyword in w for w in warnings), f"{keyword} 오탐: {warnings}"


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


# ── find_shared_sentences ──
SHARED = "탐구 과정을 통해 데이터를 분석하고 결론을 도출함"


def test_find_shared_sentences_reports_all_students():
    items = [
        ("학생A", f"{SHARED}. 발표 자료를 스스로 제작함."),
        ("학생B", f"수업에 대한 궁금증을 기록함. {SHARED}."),
        ("학생C", f"{SHARED}"),
    ]
    result = find_shared_sentences(items)
    assert len(result) == 1
    sentence, names = result[0]
    assert SHARED in sentence
    assert sorted(names) == ["학생A", "학생B", "학생C"]


def test_find_shared_sentences_ignores_short_sentences():
    items = [("학생A", "성실함. 노력함."), ("학생B", "성실함. 노력함.")]
    assert find_shared_sentences(items) == []


def test_find_shared_sentences_ignores_repeat_within_one_student():
    items = [
        ("학생A", f"{SHARED}. {SHARED}."),
        ("학생B", "완전히 다른 내용으로 봉사활동 경험을 구체적으로 서술함."),
    ]
    assert find_shared_sentences(items) == []


def test_find_shared_sentences_unique_texts_return_empty():
    items = [
        ("학생A", "빛의 굴절 실험을 설계하고 오차 원인을 스스로 분석함."),
        ("학생B", "지역 하천의 수질 자료를 수집해 그래프로 정리함."),
    ]
    assert find_shared_sentences(items) == []


def test_find_shared_sentences_sorted_by_student_count_desc():
    common = "모둠 활동에서 자료 조사를 맡아 근거를 정리함"
    items = [
        ("학생A", f"{common}. {SHARED}."),
        ("학생B", f"{common}. {SHARED}."),
        ("학생C", f"{common}."),
    ]
    result = find_shared_sentences(items)
    assert [len(names) for _, names in result] == [3, 2]
    assert common in result[0][0]
