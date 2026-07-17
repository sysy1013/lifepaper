# -*- coding: utf-8 -*-
import io
import zipfile

import pandas as pd

import pytest

from core.parsing import (
    build_eval_template,
    extract_docx_text,
    extract_hwpx_text,
    extract_pdf_text,
    file_stem,
    guess_roster_columns,
    parse_eval_table,
    parse_roster_table,
    read_uploaded_file,
    unique_names,
)


def test_file_stem_strips_known_extensions():
    assert file_stem("홍길동.hwp") == "홍길동"
    assert file_stem("자기평가서.HWPX") == "자기평가서"
    assert file_stem("메모.txt") == "메모"
    assert file_stem("보고서.pdf") == "보고서"
    # 알 수 없는 확장자는 그대로 둔다.
    assert file_stem("데이터.xlsx") == "데이터.xlsx"


def test_unique_names_suffixes_duplicates():
    assert unique_names(["김철수", "김철수", "이영희", "김철수"]) == [
        "김철수",
        "김철수 (2)",
        "이영희",
        "김철수 (3)",
    ]


def _build_hwpx(section_xml_by_name: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, xml in section_xml_by_name.items():
            zf.writestr(name, xml)
    return buf.getvalue()


def test_extract_hwpx_text_reads_sections_in_order():
    # extract_hwpx_text: </hp:p> → 줄바꿈, 나머지 태그 제거, HTML 엔티티 복원.
    section0 = (
        "<hml><hp:p><hp:run><hp:t>첫째 문단</hp:t></hp:run></hp:p>"
        "<hp:p><hp:run><hp:t>둘째 &amp; 문단</hp:t></hp:run></hp:p></hml>"
    )
    section1 = "<hml><hp:p><hp:run><hp:t>다른 섹션</hp:t></hp:run></hp:p></hml>"
    data = _build_hwpx(
        {
            "Contents/section1.xml": section1,
            "Contents/section0.xml": section0,
            "Contents/header.xml": "<ignored/>",
        }
    )
    text = extract_hwpx_text(data)
    # 섹션은 번호순(section0 먼저)으로 정렬된다.
    assert "첫째 문단" in text
    assert "둘째 & 문단" in text  # &amp; → &
    assert "다른 섹션" in text
    assert text.index("첫째 문단") < text.index("다른 섹션")


def test_extract_hwpx_text_empty_when_no_sections():
    data = _build_hwpx({"Contents/header.xml": "<x/>"})
    assert extract_hwpx_text(data).strip() == ""


# ──────────────────────────────────────────────
# .docx 추출
# ──────────────────────────────────────────────
def _build_docx(document_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_extract_docx_text_joins_runs_and_separates_paragraphs():
    # w:p 문단 2개. 첫 문단은 두 개의 w:t 런으로 나뉘어 있다.
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document_xml = (
        f'<w:document xmlns:w="{ns}"><w:body>'
        f"<w:p><w:r><w:t>첫째 </w:t></w:r><w:r><w:t>문단</w:t></w:r></w:p>"
        f"<w:p><w:r><w:t>둘째 문단</w:t></w:r></w:p>"
        f"</w:body></w:document>"
    )
    text = extract_docx_text(_build_docx(document_xml))
    # 같은 문단의 런은 이어 붙고, 문단은 줄바꿈으로 구분된다.
    assert text == "첫째 문단\n둘째 문단"


# ──────────────────────────────────────────────
# 명렬표 파싱
# ──────────────────────────────────────────────
def test_parse_roster_table_drops_empty_dedups_and_keeps_order():
    df = pd.DataFrame(
        {
            "이름": ["김철수", "이영희", "  ", "김철수", None],
            "내용": ["내용A", "내용B", "내용C", "내용D", "내용E"],
        }
    )
    result = parse_roster_table(df, "이름", "내용")
    # 이름이 공백/NaN인 행은 제외, 중복 이름은 unique_names로 순번, 순서 유지.
    assert result == [
        ("김철수", "내용A"),
        ("이영희", "내용B"),
        ("김철수 (2)", "내용D"),
    ]


def test_parse_roster_table_drops_empty_text():
    df = pd.DataFrame({"이름": ["김철수", "이영희"], "내용": ["내용A", "   "]})
    assert parse_roster_table(df, "이름", "내용") == [("김철수", "내용A")]


def test_guess_roster_columns_picks_short_name_and_long_text():
    df = pd.DataFrame(
        {
            "번호": [1, 2, 3],
            "이름": ["김철수", "이영희", "박민수"],
            "자기평가": [
                "이번 학기 동안 파이썬으로 데이터를 분석하는 활동을 수행하였다.",
                "설문을 통해 자료를 수집하고 표로 정리한 뒤 발표를 진행하였다.",
                "탐구 결과를 바탕으로 후속 활동 계획을 구체적으로 세웠다.",
            ],
        }
    )
    name_col, text_col = guess_roster_columns(df)
    assert name_col == "이름"
    assert text_col == "자기평가"


# ──────────────────────────────────────────────
# .pdf 추출
# ──────────────────────────────────────────────
def test_extract_pdf_text_raises_on_no_text():
    # 빈 페이지 PDF는 텍스트 레이어가 없으므로 ValueError를 낸다.
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(ValueError):
        extract_pdf_text(buf.getvalue())


class _FakeUpload:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


def test_read_uploaded_file_routes_pdf(monkeypatch):
    # .pdf 확장자는 extract_pdf_text로 위임된다.
    called = {}

    def fake_extract(data: bytes) -> str:
        called["data"] = data
        return "PDF 본문"

    monkeypatch.setattr("core.parsing.extract_pdf_text", fake_extract)
    result = read_uploaded_file(_FakeUpload("보고서.PDF", b"%PDF-1.4 fake"))
    assert result == "PDF 본문"
    assert called["data"] == b"%PDF-1.4 fake"


# ──────────────────────────────────────────────
# 엑셀 자기평가서 파싱 / 양식
# ──────────────────────────────────────────────
def test_parse_eval_table_merges_cols_skips_empty_dedups():
    df = pd.DataFrame(
        {
            "이름": ["김철수", "이영희", "  ", "김철수", None],
            "활동": ["데이터 분석", "설문 조사", "행", "코딩", "행"],
            "느낀점": ["협업 중요", None, "느낀점만", "", "느낀점"],
        }
    )
    result = parse_eval_table(df, "이름", ["활동", "느낀점"])
    # 선택 열을 [헤더] 접두로 병합, 빈 셀은 건너뜀, 이름 공백/NaN 행 제외,
    # 중복 이름은 순번, 전부 빈 행 제외, 순서 유지.
    assert result == [
        ("김철수", "[활동] 데이터 분석\n\n[느낀점] 협업 중요"),
        ("이영희", "[활동] 설문 조사"),
        ("김철수 (2)", "[활동] 코딩"),
    ]


def test_parse_eval_table_drops_rows_with_all_empty_content():
    df = pd.DataFrame({"이름": ["김철수", "이영희"], "활동": ["활동A", "   "]})
    assert parse_eval_table(df, "이름", ["활동"]) == [("김철수", "[활동] 활동A")]


def test_build_eval_template_readable_with_expected_headers():
    df = pd.read_excel(io.BytesIO(build_eval_template()))
    assert list(df.columns) == ["이름", "활동 내용", "배우고 느낀 점", "진로 연계"]
    assert df.iloc[0]["이름"] == "김철수"
