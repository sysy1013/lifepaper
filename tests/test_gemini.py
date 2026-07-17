# -*- coding: utf-8 -*-
import json

import pytest

from core.gemini import (
    CATEGORY_GUIDES,
    DEFAULT_MODEL,
    MODEL_CHOICES,
    _gemini_json,
    _gemini_text,
    _strip_code_fence,
    build_draft_prompt,
    category_for_neis_item,
    get_active_model,
    get_fallback_models,
    quality_avg,
    rewrite_with_gemini,
    set_active_model,
)
from core.rules import NEIS_LIMITS


class _Resp:
    def __init__(self, text):
        self.text = text


class _StubModel:
    """behaviors 리스트를 순서대로 소비한다. Exception이면 raise, 문자열이면 응답 텍스트."""

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = 0
        self.model_names = []

    def generate_content(self, prompt, model_name=None):
        b = self.behaviors[self.calls]
        self.calls += 1
        self.model_names.append(model_name)
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
    # 마지막 재시도 실패 후 폴백 체인(2개)을 모두 시도하지만 전부 503으로 실패한다
    # → 총 3(주 모델) + 2(폴백) = 5회 호출.
    model = _StubModel([Exception("503 unavailable")] * 5)
    with pytest.raises(Exception):
        _gemini_text(model, "p", max_attempts=3)
    assert model.calls == 5


def test_gemini_text_no_retry_on_non_retryable():
    model = _StubModel([Exception("401 unauthorized"), "should-not-reach"])
    with pytest.raises(Exception):
        _gemini_text(model, "p")
    assert model.calls == 1


def test_gemini_text_retries_on_empty_response():
    model = _StubModel(["", "복구된 응답"])
    assert _gemini_text(model, "p") == "복구된 응답"
    assert model.calls == 2


# ── _gemini_text 폴백 ──
def test_gemini_text_falls_back_after_final_retryable_failure():
    model = _StubModel(
        [Exception("503 unavailable")] * 3 + ["폴백 응답 텍스트"]
    )
    fallback = get_fallback_models()
    assert _gemini_text(model, "p", max_attempts=3) == "폴백 응답 텍스트"
    assert model.calls == 4
    assert model.model_names[-1] == fallback[0]


def test_gemini_text_falls_back_to_second_model_when_first_fails():
    # 첫 번째 폴백(fallback[0])도 503으로 실패하면 두 번째 폴백을 시도한다.
    model = _StubModel(
        [Exception("503 unavailable")] * 3
        + [Exception("503 unavailable"), "두 번째 폴백 응답"]
    )
    fallback = get_fallback_models()
    assert _gemini_text(model, "p", max_attempts=3) == "두 번째 폴백 응답"
    assert model.calls == 5
    assert model.model_names[-1] == fallback[1]


def test_gemini_text_no_fallback_on_non_retryable():
    model = _StubModel([Exception("401 unauthorized"), "should-not-reach"])
    with pytest.raises(Exception):
        _gemini_text(model, "p")
    assert model.calls == 1


def test_gemini_text_fallback_also_fails_raises_original_error():
    # 주 모델 재시도(3회) + 폴백 체인(2개) 모두 503으로 실패 → 총 5회 호출.
    fallback = get_fallback_models()
    model = _StubModel([Exception("503 unavailable")] * (3 + len(fallback)))
    with pytest.raises(Exception, match="503"):
        _gemini_text(model, "p", max_attempts=3)
    assert model.calls == 3 + len(fallback)
    assert model.model_names[-1] == fallback[-1]


# ── 활성 모델 선택 ──
def test_set_get_active_model_roundtrip():
    try:
        for label, model_name in MODEL_CHOICES.items():
            set_active_model(model_name)
            assert get_active_model() == model_name
    finally:
        set_active_model(DEFAULT_MODEL)


def test_get_fallback_models_excludes_active():
    try:
        for model_name in MODEL_CHOICES.values():
            set_active_model(model_name)
            fallback = get_fallback_models()
            assert model_name not in fallback
    finally:
        set_active_model(DEFAULT_MODEL)


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


# ── NEIS 항목 → 작성 카테고리 매핑 ──
def test_category_for_neis_item_all_keys_resolve():
    expected = {
        "과목별 세특 (500자)": "세특",
        "개인별 세특 (500자)": "세특",
        "자율·자치활동 (500자)": "자율·자치활동",
        "동아리활동 (500자)": "동아리활동",
        "진로활동 (700자)": "진로활동",
        "행동특성 및 종합의견 (500자)": "행동특성 및 종합의견",
        "제한 없음": "세특",
    }
    for item in NEIS_LIMITS:
        cat = category_for_neis_item(item)
        assert cat == expected[item]
        # 세특 외 카테고리는 작성 원칙이 정의되어 있어야 한다
        if cat != "세특":
            assert cat in CATEGORY_GUIDES


def test_category_for_neis_item_unknown_defaults_to_세특():
    assert category_for_neis_item("알 수 없는 항목") == "세특"
    assert category_for_neis_item("") == "세특"


def test_build_draft_prompt_category_injects_guide():
    p = build_draft_prompt(
        "무제", "미입력", "수행", "", 500, category="행동특성 및 종합의견"
    )
    assert "[작성 항목]" in p
    assert "행동특성 및 종합의견" in p
    assert CATEGORY_GUIDES["행동특성 및 종합의견"] in p


def test_build_draft_prompt_default_category_omits_guide():
    p = build_draft_prompt("정보", "컴퓨터공학", "수행", "", 500)
    assert "[작성 항목]" in p
    assert "[이 항목의 작성 원칙]" not in p


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


# ── rewrite_with_gemini ──
def test_rewrite_retries_when_first_pass_still_dirty(monkeypatch):
    model = _StubModel(["TOEIC 900점을 받은 학생입니다.", "영어 실력이 뛰어난 학생입니다."])
    monkeypatch.setattr("core.gemini._make_model", lambda *a, **k: model)

    result = rewrite_with_gemini("원문", [], "영어영문학과", "fake-key")

    assert model.calls == 2
    assert result == "영어 실력이 뛰어난 학생입니다."


def test_rewrite_single_pass_when_already_clean(monkeypatch):
    model = _StubModel(["성실하게 탐구 활동에 참여함."])
    monkeypatch.setattr("core.gemini._make_model", lambda *a, **k: model)

    result = rewrite_with_gemini("원문", [], "영어영문학과", "fake-key")

    assert model.calls == 1
    assert result == "성실하게 탐구 활동에 참여함."


# ── _strip_code_fence ──
def test_strip_code_fence_variants():
    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence("```\nplain\n```") == "plain"
    assert _strip_code_fence("  이미 깔끔한 텍스트  ") == "이미 깔끔한 텍스트"
