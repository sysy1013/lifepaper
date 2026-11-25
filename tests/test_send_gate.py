# -*- coding: utf-8 -*-
"""공통 전송 게이트(render_risk_gate)·자동 마스킹 끔·모두 지우기 AppTest."""
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 실제 키 형식이 아닌 테스트 전용 값
FAKE_KEY = "test-" + "not-a-real-key"
RISKY = "수원시 장안구 하천의 수질을 조사하고 결과를 정리함."


@pytest.fixture
def app():
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    old = os.getcwd()
    os.chdir(ROOT)
    try:
        at = AppTest.from_file("app.py", default_timeout=60)
        at.secrets["GEMINI_API_KEY"] = FAKE_KEY
        at.session_state["auth_ok"] = True
        at.run()
        yield at
    finally:
        os.chdir(old)


def _btn(at, text):
    return next(b for b in at.button if text in str(b.label))


def _paste(at, text):
    method = next(r for r in at.radio if "직접 붙여넣기" in r.options)
    method.set_value("직접 붙여넣기").run()
    at.text_area(key="input_text").input(text).run()


def test_single_review_gate_unchanged(app):
    _paste(app, RISKY)
    assert _btn(app, "검토 실행").disabled
    app.checkbox(key="risk_ack").check().run()
    assert not _btn(app, "검토 실행").disabled


def test_pre_review_proofread_follows_review_gate(app):
    _paste(app, RISKY)
    assert _btn(app, "오탈자 검사 실행").disabled
    app.checkbox(key="risk_ack").check().run()
    assert not _btn(app, "오탈자 검사 실행").disabled


def test_auto_mask_off_forces_gate_even_without_risk(app):
    _paste(app, "실험 결과를 분석해 그래프로 정리함.")
    assert not _btn(app, "검토 실행").disabled
    app.toggle(key="auto_mask").set_value(False).run()
    assert any("자동 마스킹이 꺼져" in str(w.value) for w in app.warning)
    assert _btn(app, "검토 실행").disabled
    app.checkbox(key="risk_ack").check().run()
    assert not _btn(app, "검토 실행").disabled


def test_draft_generation_gate(app):
    mode = next(r for r in app.radio if "✍️ 세특 초안 작성" in r.options)
    mode.set_value("✍️ 세특 초안 작성").run()
    app.text_area(key="performance_text").input(RISKY).run()
    assert _btn(app, "초안 생성").disabled
    app.checkbox(key="risk_ack_draft").check().run()
    assert not _btn(app, "초안 생성").disabled


def test_draft_post_tools_are_gated(app):
    mode = next(r for r in app.radio if "✍️ 세특 초안 작성" in r.options)
    mode.set_value("✍️ 세특 초안 작성").run()
    app.session_state["draft_text"] = RISKY
    app.run()
    for label in ("피드백 반영하여 다시 쓰기", "품질 진단 실행", "오탈자 검사 실행", "분량 맞추기"):
        assert _btn(app, label).disabled, label
    app.checkbox(key="risk_ack_refine").check().run()
    assert not _btn(app, "피드백 반영하여 다시 쓰기").disabled
    assert _btn(app, "품질 진단 실행").disabled  # 표면마다 따로 확인


def test_clear_all_removes_history_undo_and_cache(app):
    app.session_state["history"] = [{"time": "t", "label": "x", "content": "본문"}]
    app.session_state["_upload_text_cache"] = {("id", 1): "본문"}
    app.session_state["draft_text"] = "초안"
    app.run()
    app.button(key="clear_all").click().run()
    left = set(app.session_state.filtered_state)
    assert "history" not in left or not app.session_state["history"]
    assert "_upload_text_cache" not in left
    assert not any(str(k).startswith(("_undo", "undo_")) for k in left)
    assert "draft_text" not in left


def test_public_pc_notice_visible(app):
    texts = [str(m.value) for m in app.markdown]
    assert any("탭을 닫아도 서버 메모리에 일정 시간 남을 수 있으니" in t for t in texts)
