# -*- coding: utf-8 -*-
import json

import pytest

from core.gemini import (
    _gemini_json,
    _gemini_text,
    _strip_code_fence,
    build_draft_prompt,
    quality_avg,
)


class _Resp:
    def __init__(self, text):
        self.text = text


class _StubModel:
    """behaviors 리스트를 순서대로 소비한다. Exception이면 raise, 문자열이면 응답 텍스트."""

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = 0

    def generate_content(self, prompt):
        b = self.behaviors[self.calls]
        self.calls += 1
        if isinstance(b, Exception):
            raise b
        return _Resp(b)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # _gemini_text는 core.gemini에서 import한 time 모듈의 sleep을 참조한다.
    monkeypatch.setattr("core.gemini.time.sleep", lambda *a, **k: None)


# ── _gemini_text ──
def test_gemini_text_retries_on_retryable_then_succeeds():
    model = _StubModel([Exception("429 rate limit exceeded"), "정상 응답"])
    assert _gemini_text(model, "p") == "정상 응답"
    assert model.calls == 2


def test_gemini_text_gives_up_after_max_attempts():
    model = _StubModel([Exception("503 unavailable")] * 5)
    with pytest.raises(Exception):
        _gemini_text(model, "p", max_attempts=3)
    assert model.calls == 3


def test_gemini_text_no_retry_on_non_retryable():
    model = _StubModel([Exception("401 unauthorized"), "should-not-reach"])
    with pytest.raises(Exception):
        _gemini_text(model, "p")
    assert model.calls == 1


def test_gemini_text_retries_on_empty_response():
    model = _StubModel(["", "복구된 응답"])
    assert _gemini_text(model, "p") == "복구된 응답"
    assert model.calls == 2


# ── _gemini_json ──
def test_gemini_json_recovers_from_one_bad_parse():
    model = _StubModel(["이건 JSON이 아님", '{"a": 1}'])
    assert _gemini_json(model, "p") == {"a": 1}
    assert model.calls == 2


def test_gemini_json_strips_json_fence():
    model = _StubModel(['```json\n{"ok": true}\n```'])
    assert _gemini_json(model, "p") == {"ok": True}


def test_gemini_json_raises_after_two_bad_parses():
    model = _StubModel(["엉망1", "엉망2"])
    with pytest.raises(json.JSONDecodeError):
        _gemini_json(model, "p")
    assert model.calls == 2


# ── quality_avg ──
def test_quality_avg_normal():
    q = {"scores": [{"score": 4}, {"score": 2}, {"score": 3}]}
    assert quality_avg(q) == pytest.approx(3.0)


def test_quality_avg_empty():
    assert quality_avg({}) is None
    assert quality_avg({"scores": []}) is None


# ── build_draft_prompt ──
_NO_COPY = "예시에 담긴 내용·사실·활동은 절대 가져오지 않는다"


def test_build_draft_prompt_includes_core_fields():
    p = build_draft_prompt("정보", "컴퓨터공학", "수행평가함", "", 500)
    assert "정보" in p
    assert "컴퓨터공학" in p
    assert "500" in p


def test_build_draft_prompt_self_eval_branches():
    with_eval = build_draft_prompt("수학", "수학과", "수행", "자기평가서 내용", 400)
    assert "학생 자기평가서 원문" in with_eval
    assert "자기평가서 내용을 우선 활용" in with_eval

    without_eval = build_draft_prompt("수학", "수학과", "수행", "", 400)
    assert "학생 자기평가서 원문" not in without_eval
    assert "학생 자기평가서가 없으므로" in without_eval


def test_build_draft_prompt_with_style_examples():
    p = build_draft_prompt(
        "정보", "컴퓨터공학", "수행", "", 500,
        style_examples=["잘 쓴 세특 예시 문장임.", "두 번째 예시임."],
    )
    assert "[문체 참고 예시 1]" in p
    assert "[문체 참고 예시 2]" in p
    assert "잘 쓴 세특 예시 문장임." in p
    assert _NO_COPY in p


def test_build_draft_prompt_without_style_examples():
    p = build_draft_prompt("정보", "컴퓨터공학", "수행", "", 500)
    assert "[문체 참고 예시 1]" not in p
    assert _NO_COPY not in p

    # 빈/공백 예시는 무시된다
    p2 = build_draft_prompt(
        "정보", "컴퓨터공학", "수행", "", 500, style_examples=["", "   "]
    )
    assert "[문체 참고 예시 1]" not in p2
    assert _NO_COPY not in p2


# ── _strip_code_fence ──
def test_strip_code_fence_variants():
    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence("```\nplain\n```") == "plain"
    assert _strip_code_fence("  이미 깔끔한 텍스트  ") == "이미 깔끔한 텍스트"
