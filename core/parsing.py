# -*- coding: utf-8 -*-
"""파일 텍스트 추출 (.txt / .hwp / .hwpx) 및 파일명 유틸리티."""

import html
import io
import re
import zipfile
import zlib
from collections import Counter

import olefile


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


def read_uploaded_file(uploaded_file) -> str:
    """업로드 파일의 확장자에 따라 텍스트를 추출한다."""
    name = uploaded_file.name.lower()
    # read()는 재실행 시 포인터가 끝에 있어 빈 값이 될 수 있으므로 getvalue() 사용
    data = uploaded_file.getvalue()
    if name.endswith(".hwp"):
        return extract_hwp_text(data)
    if name.endswith(".hwpx"):
        return extract_hwpx_text(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp949")


def file_stem(name: str) -> str:
    return re.sub(r"\.(hwp|hwpx|txt)$", "", name, flags=re.IGNORECASE)


def unique_names(names: list[str]) -> list[str]:
    """중복된 파일명(stem)에 순번을 붙여 위젯 key·ZIP 내 파일명 충돌을 방지한다."""
    seen: Counter = Counter()
    out = []
    for n in names:
        seen[n] += 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    return out
