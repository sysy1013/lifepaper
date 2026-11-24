# -*- coding: utf-8 -*-
"""통합 오류 리포트(core/report.py) + 공식 근거 규칙 사전(core/rulebook.py) 테스트."""

import pytest

from core.parsing import split_by_subject
from core.report import (
    REPORT_COLUMNS,
    build_issue_report,
    report_table_rows,
    summarize_report,
)
from core.rulebook import (
    RULEBOOK,
    THESIS_SUBJECTS,
    apply_exceptions,
    inside_book_title,
    is_education_institution,
    is_thesis_subject,
    rule_id_for_finding,
)
from core.rules import RULE_PATTERNS, collect_style_issues, rule_based_filter


def _f(word, severity="위반", **kw):
    base = {
        "word": word,
        "reason": kw.pop("reason", "사유"),
        "basis": kw.pop("basis", "근거"),
        "severity": severity,
        "suggestion_1": "대안1",
        "suggestion_2": "대안2",
    }
    base.update(kw)
    return base


# ── 위치 계산 ──
def test_multiple_occurrences_get_separate_rows():
    text = "TOEIC 공부함.\n다시 TOEIC 응시함."
    rows = [r for r in build_issue_report(text, [_f("TOEIC", rule_id="language_test")]) if r["category"] == "기재금지"]
    assert [(r["line"], r["sentence"], r["start"]) for r in rows] == [(1, 1, 0), (2, 1, 14)]
    assert all("「TOEIC」" in r["excerpt"] for r in rows)


def test_sentence_index_in_single_long_line():
    text = "첫 문장임. 둘째 문장임. 셋째 문장에서 구글을 활용함. 넷째 문장임."
    rows = [r for r in build_issue_report(text, [_f("구글", rule_id="brand")]) if r["category"] == "기재금지"]
    assert len(rows) == 1
    assert rows[0]["line"] == 1
    assert rows[0]["sentence"] == 3


def test_decimal_point_is_not_sentence_boundary():
    text = "평균 3.5점 차이를 확인함. 구글 자료를 봄."
    rows = [r for r in build_issue_report(text, [_f("구글", rule_id="brand")]) if r["category"] == "기재금지"]
    assert rows[0]["sentence"] == 2


def test_period_belongs_to_its_sentence():
    text = "자료를 정리함.. 발표함."
    rows = [r for r in build_issue_report(text, []) if r["rule_id"] == "format_double_punct"]
    assert rows and rows[0]["sentence"] == 1


def test_uncertain_mark_rows_and_positions():
    text = "정보: 가나⟦?⟧다 탐구함.\n수학: 라마⟦?⟧바 정리함."
    rows = [r for r in build_issue_report(text, []) if r["rule_id"] == "uncertain_char"]
    assert [(r["line"], r["start"]) for r in rows] == [(1, 6), (2, 22)]
    assert all(r["verdict"] == "확인 필요" for r in rows)


def test_word_after_uncertain_mark_offsets_stay_exact():
    text = "가⟦?⟧ 탐구 중 구글을 씀."
    rows = [r for r in build_issue_report(text, [_f("구글", rule_id="brand")]) if r["category"] == "기재금지"]
    assert text[rows[0]["start"]:rows[0]["end"]] == "구글"


def test_rows_sorted_by_line_sentence_start():
    text = "몇일 동안 TOEIC 공부함. 구글 사용함.\n궁금한것은 질문함."
    rows = build_issue_report(
        text, [_f("구글", rule_id="brand"), _f("TOEIC", rule_id="language_test")]
    )
    keys = [(r["line"], r["sentence"], r["start"]) for r in rows]
    assert keys == sorted(keys)


# ── 판정 ──
def test_verdict_mapping_and_summary():
    text = "구글 활용함. 학원 자료임. 몇일 동안 최고의 성과를 냄."
    rows = build_issue_report(
        text,
        [_f("구글", "위반", rule_id="brand"), _f("학원", "주의", reason="기관명")],
    )
    by_word = {r["word"]: r["verdict"] for r in rows}
    assert by_word["구글"] == "기재 불가"
    assert by_word["학원"] == "확인 필요"
    assert by_word["몇일"] == "수정 필요"
    assert by_word["최고의"] == "권고"
    summary = summarize_report(rows)
    assert "기재 불가 1건" in summary and "확인 필요 1건" in summary
    assert "최종 판단은 교사" in summary


def test_ignored_words_excluded():
    text = "구글 활용함. 몇일 동안 기록함."
    rows = build_issue_report(text, [_f("구글", rule_id="brand")], ignored={"구글"})
    assert all(r["word"] != "구글" for r in rows)
    assert any(r["word"] == "몇일" for r in rows)


def test_finding_not_in_text_still_reported():
    rows = build_issue_report("본문임.", [_f("의사인 아버지", reason="부모의 직업 암시")])
    row = next(r for r in rows if r["category"] == "기재금지")
    assert row["line"] is None and row["excerpt"] == "「의사인 아버지」"


def test_proofread_items_located_and_deduped_with_local_rule():
    text = "몇일 동안 수업시간에 기록함."
    rows = build_issue_report(
        text,
        [],
        proofread_items=[
            {"wrong": "몇일", "correct": "며칠", "reason": "맞춤법"},
            {"wrong": "수업시간", "correct": "수업 시간", "reason": "띄어쓰기"},
        ],
    )
    words = [(r["word"], r["rule_id"]) for r in rows if r["category"] == "맞춤법"]
    assert ("몇일", "spelling") in words
    assert ("몇일", "proofread") not in words  # 로컬 규칙과 같은 위치 → 중복 제외
    assert ("수업시간", "proofread") in words


# ── 과목 분리: 줄 번호는 원문 전체 기준 ──
def test_subject_mode_lines_are_whole_text_based():
    text = "(1학기)정보: 구글 활용함.\n(1학기)물리학: 구글 자료를 정리함. 궁금한것은 질문함."
    segs = split_by_subject(text)
    findings = []
    for subj, content in segs:
        for f in rule_based_filter(content):
            findings.append({**f, "subject": subj})
    rows = build_issue_report(text, findings, subject_segments=segs)
    brand = [r for r in rows if r["word"] == "구글"]
    assert [(r["subject"], r["line"]) for r in brand] == [("정보", 1), ("물리학", 2)]
    geot = next(r for r in rows if r["word"] == "한것")
    assert geot["subject"] == "물리학" and geot["line"] == 2 and geot["sentence"] == 2
    assert text[geot["start"]:geot["end"]] == "한것"


def test_report_table_rows_columns_and_exception_in_basis():
    rows = build_issue_report("TOEIC 공부함.", [_f("TOEIC", rule_id="language_test")])
    table = report_table_rows(rows)
    assert table and list(table[0].keys()) == list(REPORT_COLUMNS)
    eng = next(t for t, r in zip(table, rows) if r["rule_id"] == "english")
    assert "예외:" in eng["근거"]


# ── rulebook ──
def test_every_rule_pattern_and_style_issue_links_to_rulebook():
    for *_, rule_id in RULE_PATTERNS:
        assert rule_id in RULEBOOK
    sample = "실험을 진행하였다. ★ 3~4시간 TOEIC 공부함.. 몇일 최고의  결과 '인용 (괄호"
    for issue in collect_style_issues(sample):
        assert issue["rule_id"] in RULEBOOK, issue
    for entry in RULEBOOK.values():
        assert {"title", "summary", "exceptions", "source"} <= set(entry)


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("상업적 명칭/특정 브랜드", "brand"),
        ("교외상/교외 대회 수상 실적", "outside_award"),
        ("특정 대학/기관/강사명", "university_name"),
        ("부모의 직업 암시", "parent_status"),
        ("해외 활동", "overseas"),
        ("논문/출판/특허 실적", "paper_book"),
        ("장학생/장학금 관련 내용", "scholarship"),
        ("재학 중인 학교 명칭", "school_name"),
        ("K-MOOC/MOOC/KOCW, 방과후학교 활동, 소논문 실적", "thesis"),
        ("방과후학교 활동", "mooc_afterschool"),
        ("알 수 없는 사유", "gemini_other"),
    ],
)
def test_gemini_reason_maps_to_rule_id(reason, expected):
    assert rule_id_for_finding({"word": "x", "reason": reason, "basis": ""}) == expected


@pytest.mark.parametrize(
    "word", ["교육부", "서울특별시교육청", "경기도교육청", "수원교육지원청", "충청남도교육연구정보원"]
)
def test_education_institution_allowed(word):
    assert is_education_institution(word)
    _, exc, excluded = apply_exceptions("institution", "기재 불가", word, word, 0, len(word), "")
    assert excluded and "교육관련기관" in exc


@pytest.mark.parametrize("word", ["통계청", "유네스코", "세계보건기구", "교육방송", "OO학원"])
def test_non_education_institution_not_allowed(word):
    assert not is_education_institution(word)
    _, _, excluded = apply_exceptions("institution", "기재 불가", word, word, 0, len(word), "")
    assert not excluded


def test_education_institution_excluded_in_report():
    text = "서울특별시교육청 주관 연수에 참여함. 통계청 자료를 분석함."
    rows = build_issue_report(
        text,
        [
            _f("서울특별시교육청", reason="특정 대학/기관/강사명", rule_id="institution"),
            _f("통계청", reason="특정 대학/기관/강사명", rule_id="institution"),
        ],
    )
    words = [r["word"] for r in rows if r["category"] == "기재금지"]
    assert words == ["통계청"]


def test_book_title_exception():
    text = "『서울대 선배들의 공부법』을 읽고 정리함. 서울대 진학을 목표로 함."
    first = text.index("서울대")
    second = text.index("서울대", first + 1)
    assert inside_book_title(text, first, first + 3)
    assert not inside_book_title(text, second, second + 3)
    rows = build_issue_report(text, [_f("서울대", rule_id="university_name")])
    hits = [r["start"] for r in rows if r["word"] == "서울대"]
    assert hits == [second]


@pytest.mark.parametrize("subject", list(THESIS_SUBJECTS) + ["과학과제연구"])
def test_thesis_subject_downgraded_to_check(subject):
    assert is_thesis_subject(subject)
    verdict, exc, excluded = apply_exceptions("thesis", "기재 불가", "소논문", "소논문", 0, 3, subject)
    assert verdict == "확인 필요" and not excluded
    assert "실적(제목·연구주제·참여인원·소요시간) 제외 시 기재 가능" in exc


@pytest.mark.parametrize("subject", ["물리학", "정보", ""])
def test_thesis_other_subject_not_downgraded(subject):
    assert not is_thesis_subject(subject)
    verdict, _, _ = apply_exceptions("thesis", "기재 불가", "소논문", "소논문", 0, 3, subject)
    assert verdict == "기재 불가"


def test_thesis_exception_in_subject_report():
    text = "과학과제 연구: 소논문 작성 과정을 익힘.\n물리학: 소논문을 작성함."
    segs = split_by_subject(text)
    findings = [
        {**_f("소논문", "위반", rule_id="thesis"), "subject": s} for s, _ in segs
    ]
    rows = [r for r in build_issue_report(text, findings, subject_segments=segs) if r["word"] == "소논문"]
    by_subject = {r["subject"]: r for r in rows}
    assert by_subject["과학과제 연구"]["verdict"] == "확인 필요"
    assert "제외 시 기재 가능" in by_subject["과학과제 연구"]["exception"]
    assert by_subject["물리학"]["verdict"] == "기재 불가"


def test_generalized_english_abbreviation_allowed():
    issues = collect_style_issues("AI와 SNS, PPT를 활용함. Photoshop으로 제작함.")
    eng = [i["word"] for i in issues if i["rule_id"] == "english"]
    assert eng == ["Photoshop"]
