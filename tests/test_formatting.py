# -*- coding: utf-8 -*-
import io

import pandas as pd

from core.formatting import (
    HIGHLIGHT_STYLE_CAUTION,
    build_batch_workbook,
    build_neis_workbook,
    build_student_report,
    findings_to_df,
    highlight_text,
)


def test_highlight_wraps_matched_word_in_span():
    out = highlight_text("금지어가 포함됨", ["금지어"])
    assert "<span" in out
    assert "background-color: yellow" in out
    assert "금지어" in out


def test_highlight_escapes_html_in_plain_text():
    out = highlight_text("<script>alert(1)</script>", [])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_highlight_escapes_html_inside_match():
    out = highlight_text("코드 <b>강조</b> 부분", ["<b>강조</b>"])
    assert "&lt;b&gt;강조&lt;/b&gt;" in out
    # 원문 태그가 이스케이프되지 않은 채로 새어 나오면 안 된다.
    assert "<b>" not in out


def test_highlight_overlapping_words_single_pass_prefers_longer():
    out = highlight_text("우리학교 방문", ["학교", "우리학교"])
    # 긴 단어가 우선 매치되어 span은 하나만 생긴다.
    assert out.count("<span") == 1
    assert ">우리학교<" in out


def test_highlight_preserves_newlines_as_br():
    out = highlight_text("첫줄\n둘째줄", [])
    assert "<br>" in out


def test_highlight_severities_produce_both_style_variants():
    out = highlight_text(
        "위반어와 주의어가 함께 있음",
        ["위반어", "주의어"],
        {"위반어": "위반", "주의어": "주의"},
    )
    # 위반 스타일(빨강/노랑 배경)과 주의 스타일(연노랑)이 모두 나타난다.
    assert "background-color: yellow" in out
    assert HIGHLIGHT_STYLE_CAUTION in out


def test_highlight_default_severity_uses_violation_style():
    # severities 미지정 시 기존 위반 스타일만 사용된다.
    out = highlight_text("주의어 포함", ["주의어"])
    assert "background-color: yellow" in out
    assert HIGHLIGHT_STYLE_CAUTION not in out


def test_findings_to_df_column_names():
    df = findings_to_df(
        [
            {
                "word": "TOEIC",
                "reason": "공인어학시험",
                "severity": "위반",
                "suggestion_1": "s1",
                "suggestion_2": "s2",
                "source": "규칙 기반",
            }
        ]
    )
    assert list(df.columns) == [
        "구분",
        "발견된 표현",
        "위반 사유",
        "대체 추천 1",
        "대체 추천 2",
        "검출 단계",
    ]


def test_findings_to_df_fills_missing_severity_and_source():
    df = findings_to_df(
        [{"word": "w", "reason": "r", "suggestion_1": "a", "suggestion_2": "b"}]
    )
    assert df.iloc[0]["구분"] == "위반"
    assert df.iloc[0]["검출 단계"] == "-"


def _full_report_dict():
    return {
        "name": "20301_홍길동",
        "text": "의사인 아버지의 영향을 받아 TOEIC 900점을 취득함.",
        "findings": [
            {
                "word": "TOEIC",
                "reason": "공인어학시험",
                "severity": "위반",
                "suggestion_1": "영어 실력",
                "suggestion_2": "영어 역량",
            }
        ],
        "revised": "영어 역량을 꾸준히 길러 온 학생임.",
        "quality": {
            "scores": [
                {"criterion": "구체성", "score": 4, "comment": "구체적임"},
                {"criterion": "개별성", "score": 3, "comment": "보통"},
            ],
            "overall": "전반적으로 양호함",
            "improvements": ["탐구 과정 보강", "진로 연계 강화"],
        },
        "proofread": [
            {"wrong": "취득함", "correct": "취득하였음", "reason": "어미"},
        ],
    }


def test_build_student_report_full_renders_all_sections_in_order():
    report = build_student_report(_full_report_dict(), neis_limit=1500)
    # 헤더
    assert "생기부 검토 리포트 — 20301_홍길동" in report
    assert "글자 수 (공백 포함)" in report
    assert "NEIS 제한 이내" in report
    # 섹션 존재
    assert "[검출된 기재 금지 표현]" in report
    assert "「TOEIC」" in report
    assert "영어 실력 / 영어 역량" in report
    assert "[수정본]" in report
    assert "영어 역량을 꾸준히" in report
    assert "[품질 진단]" in report
    assert "구체성 4/5" in report
    assert "종합 평균" in report
    assert "전반적으로 양호함" in report
    assert "탐구 과정 보강" in report
    assert "[오탈자]" in report
    assert "취득함 → 취득하였음" in report
    # 순서 확인
    order = [
        report.index("[검출된 기재 금지 표현]"),
        report.index("[수정본]"),
        report.index("[품질 진단]"),
        report.index("[오탈자]"),
    ]
    assert order == sorted(order)


def test_build_student_report_minimal_omits_optional_sections():
    report = build_student_report(
        {"name": "학생A", "text": "내용", "findings": []}
    )
    assert "[검출된 기재 금지 표현]" in report
    assert "[수정본]" not in report
    assert "[품질 진단]" not in report
    assert "[오탈자]" not in report


def test_build_student_report_empty_findings_shows_none():
    report = build_student_report(
        {"name": "학생A", "text": "내용", "findings": []}
    )
    assert "검출 없음" in report


def test_build_student_report_empty_proofread_shows_no_typo_line():
    report = build_student_report(
        {"name": "학생A", "text": "내용", "findings": [], "proofread": []}
    )
    assert "[오탈자]" in report
    assert "발견된 오탈자 없음" in report


def _batch_two_students():
    return [
        {
            "name": "학생A",
            "text": "의사인 아버지의 영향을 받아 TOEIC 900점을 취득함.",
            "findings": [
                {
                    "word": "TOEIC",
                    "reason": "공인어학시험",
                    "basis": "기재요령: 공인어학시험 성적 기재 불가",
                    "severity": "위반",
                    "suggestion_1": "영어 실력",
                    "suggestion_2": "영어 역량",
                }
            ],
        },
        {
            "name": "학생B",
            "text": "성실하게 참여함.",
            "findings": [],
        },
    ]


def test_build_batch_workbook_returns_readable_bytes():
    data = build_batch_workbook(_batch_two_students(), neis_limit=500)
    assert isinstance(data, bytes)

    summary = pd.read_excel(io.BytesIO(data), sheet_name="요약")
    assert len(summary) == 2
    assert "학생" in summary.columns

    detail = pd.read_excel(io.BytesIO(data), sheet_name="검출 상세")
    assert len(detail) == 1
    assert set(["학생", "검출어", "심각도", "사유", "근거", "추천1", "추천2"]).issubset(
        detail.columns
    )
    assert detail.iloc[0]["검출어"] == "TOEIC"


def test_build_batch_workbook_omits_revised_sheet_when_none():
    data = build_batch_workbook(_batch_two_students())
    xls = pd.ExcelFile(io.BytesIO(data))
    assert "수정본" not in xls.sheet_names


def test_build_batch_workbook_includes_revised_sheet_when_present():
    batch = _batch_two_students()
    batch[0]["revised"] = "영어 역량을 길러 온 학생임."
    data = build_batch_workbook(batch)
    xls = pd.ExcelFile(io.BytesIO(data))
    assert "수정본" in xls.sheet_names


def test_build_batch_workbook_empty_batch_summary_only():
    data = build_batch_workbook([])
    xls = pd.ExcelFile(io.BytesIO(data))
    assert xls.sheet_names == ["요약"]


def test_build_batch_workbook_summary_includes_byte_column():
    data = build_batch_workbook(_batch_two_students(), neis_limit=500)
    summary = pd.read_excel(io.BytesIO(data), sheet_name="요약")
    assert "글자 수" in summary.columns
    assert "NEIS 바이트" in summary.columns


# ── build_neis_workbook ──
def test_build_neis_workbook_prefers_revised_over_text():
    batch = [
        {"name": "학생A", "text": "원문 내용", "revised": "수정된 내용"},
    ]
    data = build_neis_workbook(batch)
    df = pd.read_excel(io.BytesIO(data), sheet_name="나이스 입력")
    assert df.iloc[0]["내용"] == "수정된 내용"


def test_build_neis_workbook_uses_text_when_revised_empty():
    batch = [
        {"name": "학생A", "text": "원문 내용", "revised": ""},
        {"name": "학생B", "text": "다른 내용"},
    ]
    data = build_neis_workbook(batch)
    df = pd.read_excel(io.BytesIO(data), sheet_name="나이스 입력")
    assert df.iloc[0]["내용"] == "원문 내용"
    assert df.iloc[1]["내용"] == "다른 내용"


def test_build_neis_workbook_skips_error_entries():
    batch = [
        {"name": "학생A", "text": "정상 내용"},
        {"name": "학생B", "text": "", "error": "읽기 실패"},
    ]
    data = build_neis_workbook(batch)
    df = pd.read_excel(io.BytesIO(data), sheet_name="나이스 입력")
    assert len(df) == 1
    assert df.iloc[0]["이름"] == "학생A"


def test_build_neis_workbook_byte_limit_flag_correct():
    # 한글 10자 = 30바이트. 제한 5자 → 바이트 제한 15 → 초과.
    batch = [
        {"name": "초과", "text": "가나다라마바사아자차"},
        {"name": "이내", "text": "가나"},
    ]
    data = build_neis_workbook(batch, neis_limit=5)
    df = pd.read_excel(io.BytesIO(data), sheet_name="나이스 입력")
    flags = dict(zip(df["이름"], df["제한 초과"]))
    assert flags["초과"] == "초과"
    assert flags["이내"] == "이내"


def test_build_neis_workbook_no_limit_shows_dash():
    batch = [{"name": "학생A", "text": "내용"}]
    data = build_neis_workbook(batch, neis_limit=0)
    df = pd.read_excel(io.BytesIO(data), sheet_name="나이스 입력")
    assert df.iloc[0]["제한 초과"] == "-"


def test_build_neis_workbook_empty_batch_has_sheet():
    data = build_neis_workbook([])
    xls = pd.ExcelFile(io.BytesIO(data))
    assert xls.sheet_names == ["나이스 입력"]
