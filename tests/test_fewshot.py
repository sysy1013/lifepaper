# -*- coding: utf-8 -*-
"""few-shot 선택·조항 발췌·Gemini 프롬프트 주입·cited_rule 검증 스텁 테스트 (API 호출 없음)."""

import json

import pytest

from core import gemini
from core.fewshot import (
    load_examples,
    resolve_cited_rule,
    rulebook_excerpt,
    select_fewshots,
    similarity,
)
from core.rulebook import RULEBOOK


class _Resp:
    def __init__(self, text):
        self.text = text


class _RecordingModel:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def generate_content(self, prompt, model_name=None):
        self.prompts.append(prompt)
        return _Resp(self.response)


@pytest.fixture
def stub(monkeypatch):
    holder = {}

    def install(response):
        model = _RecordingModel(response)

        def fake_make_model(api_key, system_instruction, temperature, json_mode=False, **k):
            holder["system"] = system_instruction
            return model

        monkeypatch.setattr("core.gemini._make_model", fake_make_model)
        holder["model"] = model
        return holder

    return install


def test_similarity_bounds():
    assert similarity("같은 문장임", "같은 문장임") == 1.0
    assert similarity("가나다라", "마바사아") == 0.0


def test_select_fewshots_k_and_diversity():
    shots = select_fewshots("약사인 이모의 조언으로 약물 부작용을 조사함.", k=6)
    assert len(shots) == 6
    assert len({s["category"] for s in shots}) == 6
    assert any(s["verdict"] == "기재 가능" for s in shots)
    assert shots[0]["category"] == "parent_status"


def test_select_fewshots_hint_category_boost():
    shots = select_fewshots("평범한 문장임.", k=3, hint_categories={"scholarship"})
    assert shots[0]["category"] == "scholarship"


def test_select_fewshots_zero():
    assert select_fewshots("문장", k=0) == []


def test_rulebook_excerpt_length_and_ids():
    ex = rulebook_excerpt(max_chars=2400)
    assert len(ex) <= 2400
    assert "parent_status" in ex and "institution" in ex
    short = rulebook_excerpt(max_chars=300)
    assert len(short) <= 300


@pytest.mark.parametrize(
    "value, rid, status",
    [
        ("parent_status", "parent_status", "ok"),
        ("부모 사회·경제적 지위", "parent_status", "ok"),
        ("made_up_rule", None, "unknown"),
        ("", None, "missing"),
        (None, None, "missing"),
        (["brand"], None, "malformed"),
        ("p.18-19 기재요령", None, "malformed"),
    ],
)
def test_resolve_cited_rule(value, rid, status):
    assert resolve_cited_rule(value) == (rid, status)


def test_prompt_contains_fewshot_and_excerpt(stub):
    h = stub("[]")
    gemini.analyze_with_gemini("약사인 고모의 조언으로 약물을 조사함.", "약학", "key")
    prompt = h["model"].prompts[0]
    assert "[판단 참고 예시" in prompt
    assert "[기재요령 조항 발췌" in prompt
    assert "[심사 대상 생기부 텍스트]\n약사인 고모의 조언으로 약물을 조사함." in prompt
    assert "cited_rule" in h["system"]
    assert h["system"].startswith(gemini.SYSTEM_PROMPT)
    # 예시 텍스트 일부가 실제로 들어감
    assert any(e["text"] in prompt for e in load_examples())


def test_use_fewshot_false_is_legacy_prompt(stub):
    h = stub("[]")
    gemini.analyze_with_gemini("본문임.", "", "key", use_fewshot=False)
    prompt = h["model"].prompts[0]
    assert h["system"] == gemini.SYSTEM_PROMPT
    assert prompt == (
        "[학생의 희망 진로/학과]\n미입력\n\n[심사 대상 생기부 텍스트]\n본문임.\n\n"
        "위 텍스트를 심사하여 JSON 배열로만 답하라."
    )
    assert "[판단 참고 예시" not in prompt


def _item(**kw):
    base = {
        "word": "약사인 고모",
        "reason": "부모 직업 암시",
        "basis": "부모 지위 (2026 기재요령 p.18-19)",
        "severity": "위반",
        "suggestion_1": "a",
        "suggestion_2": "b",
    }
    base.update(kw)
    return base


def test_cited_rule_valid_keeps_severity_and_sets_rule_id(stub):
    stub(json.dumps([_item(cited_rule="parent_status")], ensure_ascii=False))
    out = gemini.analyze_with_gemini("약사인 고모의 조언.", "", "key")
    assert out[0]["severity"] == "위반"
    assert out[0]["rule_id"] == "parent_status"
    assert out[0]["note"] == ""
    assert out[0]["cited_rule"] == "parent_status"


@pytest.mark.parametrize("cited", [None, "nonexistent_rule", "기재요령 어딘가", 42])
def test_cited_rule_missing_or_invalid_downgrades(stub, cited):
    item = _item() if cited is None else _item(cited_rule=cited)
    stub(json.dumps([item], ensure_ascii=False))
    out = gemini.analyze_with_gemini("약사인 고모의 조언.", "", "key")
    assert len(out) == 1  # 항목은 유지
    assert out[0]["severity"] == "주의"
    assert out[0]["note"] == "근거 미확인"
    assert "rule_id" not in out[0] or out[0]["rule_id"] in RULEBOOK


def test_legacy_path_returns_legacy_keys(stub):
    stub(json.dumps([_item(cited_rule="bogus")], ensure_ascii=False))
    out = gemini.analyze_with_gemini("약사인 고모의 조언.", "", "key", use_fewshot=False)
    assert set(out[0]) == {"word", "reason", "basis", "severity", "suggestion_1", "suggestion_2"}
    assert out[0]["severity"] == "위반"
