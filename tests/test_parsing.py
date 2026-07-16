# -*- coding: utf-8 -*-
import io
import zipfile

from core.parsing import extract_hwpx_text, file_stem, unique_names


def test_file_stem_strips_known_extensions():
    assert file_stem("홍길동.hwp") == "홍길동"
    assert file_stem("자기평가서.HWPX") == "자기평가서"
    assert file_stem("메모.txt") == "메모"
    # 알 수 없는 확장자는 그대로 둔다.
    assert file_stem("보고서.pdf") == "보고서.pdf"


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
