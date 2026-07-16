# -*- coding: utf-8 -*-
from core.formatting import findings_to_df, highlight_text


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
