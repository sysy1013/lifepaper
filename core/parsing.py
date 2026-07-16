# -*- coding: utf-8 -*-
"""파일 텍스트 추출 (.txt / .hwp / .hwpx / .docx) 및 파일명·명렬표 유틸리티."""

import html
import io
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter

import olefile
import pandas as pd


# ──────────────────────────────────────────────
# 파일 텍스트 추출 (.txt / .hwp / .hwpx)
# ──────────────────────────────────────────────
def _parse_hwp_body_text(data: bytes) -> str:
    """HWP 5.0 BodyText 섹션 레코드에서 본문 텍스트(HWPTAG_PARA_TEXT=67)를 추출한다."""
    out = []
    pos = 0
    n = len(data)
    while pos + 4 <= n:
        header = int.from_bytes(data[pos : pos + 4], "little")
        tag = header & 0x3FF
        size = (header >> 20) & 0xFFF
        pos += 4
        if size == 0xFFF:  # 확장 크기
            size = int.from_bytes(data[pos : pos + 4], "little")
            pos += 4
        if tag == 67:  # HWPTAG_PARA_TEXT
            chunk = data[pos : pos + size]
            i = 0
            while i + 2 <= len(chunk):
                code = int.from_bytes(chunk[i : i + 2], "little")
                if code in (10, 13):
                    out.append("\n")
                    i += 2
                elif code < 32:
                    # 문자 컨트롤(0, 24~31)은 2바이트, 인라인/확장 컨트롤은 16바이트
                    i += 2 if code in (0, 24, 25, 26, 27, 28, 29, 30, 31) else 16
                else:
                    out.append(chr(code))
                    i += 2
            out.append("\n")
        pos += size
    return "".join(out)


def extract_hwp_text(file_bytes: bytes) -> str:
    """HWP(한글 5.0) 파일에서 본문 텍스트를 추출한다. 실패 시 미리보기(PrvText)로 대체."""
    ole = olefile.OleFileIO(io.BytesIO(file_bytes))
    try:
        file_header = ole.openstream("FileHeader").read()
        compressed = bool(file_header[36] & 1)

        sections = sorted(
            (e for e in ole.listdir() if e[0] == "BodyText"),
            key=lambda e: int(re.sub(r"\D", "", e[1]) or 0),
        )
        texts = []
        for entry in sections:
            raw = ole.openstream(entry).read()
            if compressed:
                raw = zlib.decompress(raw, -15)
            texts.append(_parse_hwp_body_text(raw))

        body = "\n".join(t for t in texts if t.strip())
        if body.strip():
            return body

        if ole.exists("PrvText"):
            return ole.openstream("PrvText").read().decode("utf-16-le", errors="ignore")
        return ""
    finally:
        ole.close()


def extract_hwpx_text(file_bytes: bytes) -> str:
    """HWPX(OWPML) 파일에서 본문 텍스트를 추출한다."""
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        section_names = sorted(
            n for n in zf.namelist() if re.match(r"Contents/section\d+\.xml", n)
        )
        texts = []
        for name in section_names:
            xml = zf.read(name).decode("utf-8", errors="ignore")
            xml = re.sub(r"</hp:p>", "\n", xml)
            texts.append(re.sub(r"<[^>]+>", "", xml))
        return html.unescape("\n".join(texts))


def extract_docx_text(data: bytes) -> str:
    """DOCX(Word) 파일의 word/document.xml에서 본문 텍스트를 추출한다.

    w:p 문단 내 w:t 런(run)들을 이어 붙이고, 문단은 줄바꿈으로 구분한다.
    """
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs = []
    for para in root.iter(f"{ns}p"):
        runs = [t.text or "" for t in para.iter(f"{ns}t")]
        paragraphs.append("".join(runs))
    return "\n".join(paragraphs).rstrip()


def read_uploaded_file(uploaded_file) -> str:
    """업로드 파일의 확장자에 따라 텍스트를 추출한다."""
    name = uploaded_file.name.lower()
    # read()는 재실행 시 포인터가 끝에 있어 빈 값이 될 수 있으므로 getvalue() 사용
    data = uploaded_file.getvalue()
    if name.endswith(".hwp"):
        return extract_hwp_text(data)
    if name.endswith(".hwpx"):
        return extract_hwpx_text(data)
    if name.endswith(".docx"):
        return extract_docx_text(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp949")


def file_stem(name: str) -> str:
    return re.sub(r"\.(hwp|hwpx|txt|docx)$", "", name, flags=re.IGNORECASE)


def unique_names(names: list[str]) -> list[str]:
    """중복된 파일명(stem)에 순번을 붙여 위젯 key·ZIP 내 파일명 충돌을 방지한다."""
    seen: Counter = Counter()
    out = []
    for n in names:
        seen[n] += 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    return out


# ──────────────────────────────────────────────
# 반 전체 명렬표(.csv/.xlsx) 파싱
# ──────────────────────────────────────────────
def parse_roster_table(df, name_col: str, text_col: str) -> list[tuple[str, str]]:
    """명렬표 DataFrame에서 (이름, 내용) 목록을 만든다.

    이름 또는 내용이 비어 있는(공백·NaN) 행은 제외하고, 중복 이름은
    unique_names로 순번을 붙여 구분한다. 행 순서는 그대로 유지한다.
    """
    names: list[str] = []
    texts: list[str] = []
    for _, row in df.iterrows():
        name = row[name_col]
        text = row[text_col]
        name = "" if name is None else str(name).strip()
        text = "" if text is None else str(text).strip()
        # pandas NaN은 str() 시 "nan"이 되므로 원본이 결측인지 별도 확인
        if pd.isna(row[name_col]) or pd.isna(row[text_col]):
            continue
        if not name or not text:
            continue
        names.append(name)
        texts.append(text)
    return list(zip(unique_names(names), texts))


def guess_roster_columns(df) -> tuple[str, str]:
    """명렬표의 이름 열·내용 열을 추정한다.

    이름 열: 문자열 값의 평균 길이가 10 미만인 첫 열 (없으면 첫 열).
    내용 열: 문자열 평균 길이가 가장 긴 열 (없으면 마지막 열).
    """
    def mean_str_len(col) -> float:
        lengths = [len(str(v)) for v in df[col] if pd.notna(v)]
        return sum(lengths) / len(lengths) if lengths else 0.0

    columns = list(df.columns)
    name_col = None
    for col in columns:
        values = [v for v in df[col] if pd.notna(v)]
        if values and all(isinstance(v, str) for v in values) and mean_str_len(col) < 10:
            name_col = col
            break
    if name_col is None:
        name_col = columns[0]

    text_col = max(columns, key=mean_str_len) if columns else None
    if text_col is None:
        text_col = columns[-1]
    return name_col, text_col
