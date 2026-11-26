# -*- coding: utf-8 -*-
"""검토 결과 조립 (순수 함수) — 규칙 기반 결과 변환·병합, AI 실패 시 규칙 기반 단독 결과.

Gemini 호출이 실패해도(키 오류·크레딧 소진·할당량·네트워크) 규칙 기반 검사는 키 없이
로컬에서 돌 수 있으므로, 성공 경로와 똑같은 방식(마스킹 → 규칙 검사 → 복원)으로
규칙 결과만 담은 검토 결과를 만든다. 결과에는 ai_ok=False가 붙어 화면에서 구분한다.
"""

import json

from core.masking import apply_mask, remove_mask
from core.rules import rule_based_filter
from core.security import safe_error

RULE_SOURCE = "규칙 기반"
AI_SOURCE = "Gemini 심사"
# 결과의 토큰을 원래 단어로 복원할 필드
_UNMASK_FIELDS = ("word", "reason", "basis", "suggestion_1", "suggestion_2")

AI_FAIL_TITLE = "AI 검토 실패 — 규칙 기반 검사 결과만 표시합니다 (키·크레딧 확인 필요)"
AI_FAIL_CAPTION = "이 결과에는 AI 판단이 포함되지 않았습니다."
_JSON_FAIL_MESSAGE = "Gemini 응답을 JSON으로 파싱하지 못했습니다. 다시 시도해 주세요."


def rule_findings_for_review(text: str, custom_words, rule_findings=None) -> list[dict]:
    """규칙 기반 검출을 검토 결과 형식(severity='주의', source='규칙 기반')으로 바꾼다.

    같은 word는 하나로 합친다(뒤의 항목이 앞을 덮어쓰되 첫 등장 순서는 유지).
    rule_findings가 주어지면 다시 계산하지 않고 그대로 쓴다.
    """
    if rule_findings is None:
        rule_findings = rule_based_filter(text, custom_words)
    merged: dict[str, dict] = {}
    for item in rule_findings:
        merged[item["word"]] = {**item, "severity": "주의", "source": RULE_SOURCE}
    return list(merged.values())


def merge_findings(rule_items: list[dict], gemini_items: list[dict]) -> list[dict]:
    """규칙 결과(검토 형식)와 Gemini 결과를 word 기준으로 병합한다 (Gemini 우선)."""
    merged: dict[str, dict] = {f["word"]: f for f in rule_items}
    for item in gemini_items:
        merged[item["word"]] = {**item, "source": AI_SOURCE}
    return list(merged.values())


def unmask_findings(findings: list[dict], mask_map) -> list[dict]:
    """결과 필드의 마스킹 토큰을 원래 단어로 복원한다 (제자리 수정 후 반환)."""
    for f in findings:
        for k in _UNMASK_FIELDS:
            if f.get(k):
                f[k] = remove_mask(f[k], mask_map)
    return findings


def rules_only_review(text: str, custom_words, mask_map) -> list[dict]:
    """review_text_masked와 같은 경로(마스킹 → 규칙 검사 → 복원)로 규칙 결과만 만든다."""
    masked = apply_mask(text, mask_map or [])
    return unmask_findings(rule_findings_for_review(masked, custom_words), mask_map or [])


def rules_only_review_multi(segments, custom_words, mask_map) -> list[dict]:
    """과목별 세그먼트마다 규칙 결과를 만들고 subject를 태깅해 합친다."""
    out: list[dict] = []
    for subject, content in segments:
        for f in rules_only_review(content, custom_words, mask_map):
            f["subject"] = subject
            out.append(f)
    return out


def build_rules_only_result(
    text: str,
    segments,
    custom_words,
    mask_map,
    ai_error: str = "",
) -> dict:
    """AI 검토 실패 시 화면에 보여 줄 규칙 기반 단독 검토 결과 (ai_ok=False).

    segments는 split_by_subject 결과. 2개 이상이면 과목별로 나눠 검사한다.
    """
    segments = list(segments or [])
    if len(segments) > 1:
        findings = rules_only_review_multi(segments, custom_words, mask_map)
    else:
        findings = rules_only_review(text, custom_words, mask_map)
    return {
        "text": text,
        "findings": findings,
        "segments": segments,
        "ai_ok": False,
        "ai_error": ai_error,
    }


def ai_failure_message(e: BaseException) -> str:
    """AI 호출 예외를 화면 표시용 문구로 (키·토큰은 safe_error가 가린다)."""
    from core.gemini import GeminiCreditError

    if isinstance(e, GeminiCreditError):
        return safe_error(e, with_type=False)
    if isinstance(e, json.JSONDecodeError):
        return _JSON_FAIL_MESSAGE
    return f"Gemini API 호출 실패: {safe_error(e)}"
