# -*- coding: utf-8 -*-
from core.formatting import build_student_report, findings_to_df, highlight_text


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
