# -*- coding: utf-8 -*-
"""스캔 PDF 로컬 OCR 대체 경로 테스트 (실제 OCR·네트워크 없음)."""

import io

import pytest

import core.parsing as parsing
from core.parsing import extract_pdf_text, ocr_available


def _blank_pdf() -> bytes:
    """텍스트 레이어가 없는 1쪽 PDF 바이트."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakeReader:
    """pypdf.PdfReader 대체. extract_pdf_text가 import하는 이름을 가로챈다."""

    pages_text = ""

    def __init__(self, _stream):
        self.pages = [_FakePage(type(self).pages_text)]


def _patch_reader(monkeypatch, text: str):
    # extract_pdf_text는 함수 내부에서 `from pypdf import PdfReader`를 하므로
    # pypdf 모듈 속성을 갈아끼우는 것이 가장 깔끔하다.
    import pypdf

    reader = type("_R", (_FakeReader,), {"pages_text": text})
    monkeypatch.setattr(pypdf, "PdfReader", reader)


def test_ocr_available_false_without_tesseract_binary(monkeypatch):
    pytest.importorskip("pytesseract")
    import os
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(os.path, "isfile", lambda _p: False)
    assert ocr_available() is False


def test_ocr_available_false_without_pytesseract_module(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pytesseract":
            raise ImportError("no pytesseract")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert ocr_available() is False


def test_extract_pdf_text_skips_ocr_when_text_layer_is_long(monkeypatch):
    long_text = "학생은 파이썬으로 급식 잔반 데이터를 분석하고 결과를 발표하였다."
    assert len(long_text) >= 30
    _patch_reader(monkeypatch, long_text)

    def boom(*_a, **_kw):
        raise AssertionError("텍스트 PDF에서는 OCR을 호출하면 안 된다")

    monkeypatch.setattr(parsing, "ocr_pdf_text", boom)
    monkeypatch.setattr(parsing, "ocr_available", boom)

    assert extract_pdf_text(b"%PDF-fake") == long_text


def test_extract_pdf_text_falls_back_to_ocr_when_no_text(monkeypatch):
    _patch_reader(monkeypatch, "")
    monkeypatch.setattr(parsing, "ocr_available", lambda: True)
    monkeypatch.setattr(
        parsing, "ocr_pdf_text", lambda data, **kw: "OCR로 읽은 스캔 문서 본문입니다."
    )
    assert extract_pdf_text(b"%PDF-fake") == "OCR로 읽은 스캔 문서 본문입니다."


def test_extract_pdf_text_raises_when_ocr_unavailable(monkeypatch):
    _patch_reader(monkeypatch, "")
    monkeypatch.setattr(parsing, "ocr_available", lambda: False)
    with pytest.raises(ValueError) as exc:
        extract_pdf_text(_blank_pdf())
    assert "스캔" in str(exc.value)


def test_ocr_pdf_text_raises_runtime_error_when_unavailable(monkeypatch):
    monkeypatch.setattr(parsing, "ocr_available", lambda: False)
    with pytest.raises(RuntimeError):
        parsing.ocr_pdf_text(_blank_pdf())
