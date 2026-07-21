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


# ──────────────────────────────────────────────
# 스캔(이미지) PDF 로컬 OCR
# ──────────────────────────────────────────────
# 텍스트 레이어가 이 길이 미만이면 스캔 PDF로 보고 OCR로 넘어간다.
_PDF_TEXT_MIN_CHARS = 30

# 문서 한 덩어리를 통째로 읽게 하는 설정(--psm 6).
# 기본 자동 분할은 한글 보고서에서 줄을 통째로 놓치는 경우가 많아 실측 후 고정했다.
OCR_CONFIG = "--psm 6"


def ocr_available() -> bool:
    """tesseract 실행 파일과 pytesseract가 모두 준비되어 있는지 확인한다."""
    import os
    import shutil

    try:
        import pytesseract
    except Exception:
        return False

    cmd = getattr(getattr(pytesseract, "pytesseract", None), "tesseract_cmd", None)
    if cmd and os.path.isfile(cmd):
        return True
    return shutil.which("tesseract") is not None


def ocr_pdf_text(data: bytes, max_pages: int = 10, dpi: int = 200) -> str:
    """스캔(이미지) PDF를 페이지별로 렌더링해 로컬 OCR로 글자를 읽는다.

    이미지는 외부로 전송하지 않는다. 한국어+영어 인식을 시도한다.
    """
    if not ocr_available():
        raise RuntimeError(
            "이 서버에서는 스캔 문서 글자 인식(OCR)을 사용할 수 없습니다."
        )

    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image

    texts: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page_no in range(min(max_pages, doc.page_count)):
            pix = doc[page_no].get_pixmap(dpi=dpi)
            with Image.open(io.BytesIO(pix.tobytes("png"))) as img:
                try:
                    page_text = pytesseract.image_to_string(
                        img, lang="kor+eng", config=OCR_CONFIG
                    )
                except Exception as e:
                    # 한국어 학습 데이터(kor.traineddata)가 없으면 영어만으로 재시도
                    if "kor" not in str(e):
                        raise
                    page_text = pytesseract.image_to_string(
                        img, lang="eng", config=OCR_CONFIG
                    )
            texts.append(page_text)
    return "\n".join(texts).strip()


def extract_pdf_text(data: bytes) -> str:
    """PDF 파일에서 페이지별 텍스트를 추출해 줄바꿈으로 이어 붙인다.

    텍스트 레이어가 거의 없는 스캔 이미지 PDF는 로컬 OCR로 자동 대체한다.
    (이미지는 외부로 전송하지 않는다.) OCR도 불가능하면 ValueError를 낸다.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if len(text) >= _PDF_TEXT_MIN_CHARS:
        return text

    if ocr_available():
        try:
            ocr_text = ocr_pdf_text(data)
        except Exception:
            ocr_text = ""
        if ocr_text.strip():
            return ocr_text.strip()

    if text:
        return text

    raise ValueError(
        "PDF에서 텍스트를 찾지 못했습니다. 스캔한 이미지 PDF로 보입니다. "
        "글자를 선택할 수 있는 PDF나 한글(.hwp/.hwpx)·워드(.docx) 파일을 올려 주세요."
    )


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
    if name.endswith(".pdf"):
        return extract_pdf_text(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp949")


def file_stem(name: str) -> str:
    return re.sub(r"\.(hwp|hwpx|txt|docx|pdf)$", "", name, flags=re.IGNORECASE)


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


# ──────────────────────────────────────────────
# 엑셀 자기평가서(.csv/.xlsx) 일괄 파싱
# ──────────────────────────────────────────────
def parse_eval_table(df, name_col: str, content_cols: list[str]) -> list[tuple[str, str]]:
    """엑셀 자기평가서에서 (이름, 자기평가 텍스트) 목록을 만든다.

    선택한 문항 열들을 "[문항명] 값" 형태로 이어 붙인다(빈·NaN 셀은 건너뜀).
    이름이 비었거나 합쳐진 텍스트가 빈 행은 제외하고, 중복 이름은 순번을 붙인다.
    행 순서는 그대로 유지한다.
    """
    names: list[str] = []
    texts: list[str] = []
    for _, row in df.iterrows():
        if pd.isna(row[name_col]):
            continue
        name = str(row[name_col]).strip()
        if not name:
            continue
        parts = []
        for col in content_cols:
            if pd.isna(row[col]):
                continue
            value = str(row[col]).strip()
            if value:
                parts.append(f"[{col}] {value}")
        merged = "\n\n".join(parts)
        if not merged:
            continue
        names.append(name)
        texts.append(merged)
    return list(zip(unique_names(names), texts))


def build_eval_template() -> bytes:
    """자기평가서 일괄 업로드용 엑셀 양식(.xlsx) 바이트를 생성한다."""
    df = pd.DataFrame(
        {
            "이름": ["김철수"],
            "활동 내용": ["파이썬으로 급식 잔반 데이터를 분석함"],
            "배우고 느낀 점": ["데이터 수집의 어려움과 협업의 중요성을 배움"],
            "진로 연계": ["데이터 분석 직무에 관심이 생김"],
        }
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="자기평가서")
    return buf.getvalue()
