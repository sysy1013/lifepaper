# -*- coding: utf-8 -*-
"""AppTest: Gemini 실패 시에도 규칙 기반 결과(오류 리포트 표 등)가 표시되는지.

Gemini 호출부(core.gemini.analyze_with_gemini)를 예외를 던지도록 바꿔 실제 API는 부르지 않는다.
"""
import os

import pytest

from core import gemini
from core.review import AI_FAIL_CAPTION, AI_FAIL_TITLE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_KEY = "AQ." + "FAKEx7Kq9" + "y" * 24
TEXT = "TOEIC 900점을 취득하고 구글 클래스룸으로 과제를 제출함."


def _all_text(at) -> str:
    chunks = []

    def walk(node):
        proto = getattr(node, "proto", None)
        if proto is not None:
            chunks.append(str(proto))
        children = getattr(node, "children", None)
        if isinstance(children, dict):
            for c in children.values():
                walk(c)

    walk(at._tree)
    return "\n".join(chunks)


@pytest.fixture
def app_factory(monkeypatch):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    def make(exc):
        def boom(*a, **k):
            raise exc

        monkeypatch.setattr(gemini, "analyze_with_gemini", boom)
        old = os.getcwd()
        os.chdir(ROOT)
        try:
            at = AppTest.from_file("app.py", default_timeout=120)
            at.secrets["GEMINI_API_KEY"] = FAKE_KEY
            at.session_state["auth_ok"] = True
            at.run()
        finally:
            os.chdir(old)
        return at

    return make


def _run_in_root(fn):
    old = os.getcwd()
    os.chdir(ROOT)
    try:
        return fn()
    finally:
        os.chdir(old)


def _btn(at, text):
    return next(b for b in at.button if text in str(b.label))


def _paste_and_review(at, text):
    def go():
        method = next(r for r in at.radio if "직접 붙여넣기" in r.options)
        method.set_value("직접 붙여넣기").run()
        at.text_area(key="input_text").input(text).run()
        _btn(at, "검토 실행").click().run()

    _run_in_root(go)


def test_single_review_shows_rules_only_report_on_401(app_factory):
    at = app_factory(PermissionError(f"401 UNAUTHENTICATED api key {FAKE_KEY}"))
    _paste_and_review(at, TEXT)
    assert not at.exception
    screen = _all_text(at)
    assert FAKE_KEY not in screen and "FAKEx7Kq9" not in screen
    warnings = " ".join(str(w.value) for w in at.warning)
    assert AI_FAIL_TITLE in warnings and "401" in warnings
    assert any(AI_FAIL_CAPTION in str(c.value) for c in at.caption)
    assert any("오류 리포트" in str(s.value) for s in at.subheader)
    assert len(at.dataframe) >= 1  # 오류 리포트 표
    assert "TOEIC" in screen and "구글" in screen
    res = at.session_state["review_result"]
    assert res["ai_ok"] is False and res["findings"]
    assert not _btn(at, "검토 실행").disabled  # 바로 재시도 가능
    assert any(
        b.proto.label.startswith("📥 오류 리포트 CSV") for b in at.get("download_button")
    )


def test_single_review_credit_error_message(app_factory):
    at = app_factory(gemini.GeminiCreditError(gemini._CREDIT_MESSAGE))
    _paste_and_review(at, TEXT)
    assert not at.exception
    warnings = " ".join(str(w.value) for w in at.warning)
    assert AI_FAIL_TITLE in warnings and "크레딧" in warnings
    assert at.session_state["review_result"]["ai_ok"] is False


def test_retry_after_failure_replaces_with_ai_result(app_factory, monkeypatch):
    at = app_factory(ConnectionError("network down"))
    _paste_and_review(at, TEXT)
    assert at.session_state["review_result"]["ai_ok"] is False
    monkeypatch.setattr(gemini, "analyze_with_gemini", lambda *a, **k: [])
    _run_in_root(lambda: _btn(at, "검토 실행").click().run())
    assert not at.exception
    assert at.session_state["review_result"]["ai_ok"] is True
    assert not any(AI_FAIL_TITLE in str(w.value) for w in at.warning)


def test_batch_review_keeps_rules_only_results(app_factory):
    at = app_factory(PermissionError(f"403 PERMISSION_DENIED key={FAKE_KEY}"))

    def go():
        method = next(r for r in at.radio if any(str(o).startswith("파일 업로드") for o in r.options))
        method.set_value(next(o for o in method.options if str(o).startswith("파일 업로드"))).run()
        at.file_uploader(key="review_files").set_value(
            [
                ("a.txt", TEXT.encode("utf-8"), "text/plain"),
                ("b.txt", "실험 결과를 그래프로 정리함.".encode("utf-8"), "text/plain"),
            ]
        ).run()
        _btn(at, "검토 실행").click().run()

    _run_in_root(go)
    assert not at.exception
    screen = _all_text(at)
    assert FAKE_KEY not in screen and "FAKEx7Kq9" not in screen
    batch = at.session_state["batch_review"]
    assert [b["error"] for b in batch] == ["", ""]
    assert all(b["ai_ok"] is False for b in batch)
    assert any(f["word"] == "구글" for f in batch[0]["findings"])
    warnings = " ".join(str(w.value) for w in at.warning)
    assert AI_FAIL_TITLE in warnings
    assert not any("검토 실패: a" in str(e.value) for e in at.error)
    # 유사도 결과는 세션 캐시에 저장되고 '모두 지우기'로 사라진다
    assert at.session_state["_similarity_cache"]
    _run_in_root(lambda: at.button(key="clear_all").click().run())
    assert "_similarity_cache" not in at.session_state.filtered_state


def test_image_preprocess_cached_per_session(monkeypatch):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    from core import ocr

    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, format="PNG")
    png = buf.getvalue()
    calls = []
    real_prepare = ocr.prepare_image

    def fake_prepare(data, mime):
        calls.append(len(data))
        return real_prepare(data, mime)

    monkeypatch.setattr(ocr, "prepare_image", fake_prepare)
    monkeypatch.setattr(gemini, "transcribe_images_with_gemini", lambda parts, key: "정보: 본문을 읽음.")
    monkeypatch.setattr(gemini, "verify_transcription_with_gemini", lambda *a, **k: [])

    def go():
        at = AppTest.from_file("app.py", default_timeout=120)
        at.secrets["GEMINI_API_KEY"] = FAKE_KEY
        at.session_state["auth_ok"] = True
        at.run()
        method = next(r for r in at.radio if any("이미지로 읽기" in str(o) for o in r.options))
        method.set_value(next(o for o in method.options if "이미지로 읽기" in str(o))).run()
        at.checkbox(key="image_consent").check().run()
        at.file_uploader(key="image_files").set_value(("a.png", png, "image/png")).run()
        _btn(at, "글자 읽기").click().run()
        _btn(at, "대조 검증").click().run()
        _btn(at, "글자 읽기").click().run()
        return at

    at = _run_in_root(go)
    assert not at.exception
    assert len(calls) == 1  # 같은 파일은 세션 안에서 한 번만 전처리
    assert at.session_state["_image_parts_cache"]
    _run_in_root(lambda: at.button(key="clear_all").click().run())
    assert "_image_parts_cache" not in at.session_state.filtered_state
