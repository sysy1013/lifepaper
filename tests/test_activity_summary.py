# -*- coding: utf-8 -*-
"""수행평가 자료 요약 (core.gemini.summarize_activity_with_gemini) 테스트. 네트워크 없음."""

import pytest

from core.gemini import (
    ACTIVITY_SUMMARY_SYSTEM_PROMPT,
    summarize_activity_with_gemini,
)


class _Resp:
    def __init__(self, text):
        self.text = text


class _StubModel:
    """호출된 프롬프트를 기록하고 미리 정한 응답을 돌려주는 대역."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []
        self.system_instruction = None
        self.temperature = None

    def generate_content(self, prompt, model_name=None):
        self.prompts.append(prompt)
        return _Resp(self.reply)


@pytest.fixture
def stub(monkeypatch):
    model = _StubModel("  - 설문 조사를 실시함\n- 결과를 표로 정리함  ")

    def fake_make_model(api_key, system_instruction, temperature, json_mode=False):
        model.system_instruction = system_instruction
        model.temperature = temperature
        return model

    monkeypatch.setattr("core.gemini._make_model", fake_make_model)
    return model


def test_summary_prompt_contains_source_text_and_returns_stripped(stub):
    result = summarize_activity_with_gemini("급식 잔반량을 설문으로 조사했다.", "KEY")
    assert "급식 잔반량을 설문으로 조사했다." in stub.prompts[0]
    assert result == "- 설문 조사를 실시함\n- 결과를 표로 정리함"
    assert stub.system_instruction == ACTIVITY_SUMMARY_SYSTEM_PROMPT
    assert stub.temperature == 0.3


def test_summary_prompt_includes_subject_when_given(stub):
    summarize_activity_with_gemini("본문 내용", "KEY", subject="정보")
    assert "정보" in stub.prompts[0]
    assert "본문 내용" in stub.prompts[0]


def test_summary_prompt_omits_subject_section_when_blank(stub):
    summarize_activity_with_gemini("본문 내용", "KEY", subject="   ")
    assert "[과목명]" not in stub.prompts[0]


def test_system_prompt_forbids_fabrication():
    assert "지어내지" in ACTIVITY_SUMMARY_SYSTEM_PROMPT
