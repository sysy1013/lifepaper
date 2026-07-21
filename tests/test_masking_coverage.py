# -*- coding: utf-8 -*-
"""마스킹 누락 방지 회귀 테스트 — 검출 항목의 모든 문자열 필드가 가려지는지 확인."""

import copy

from core.masking import build_mask_map, mask_findings

NAME = "홍길동"
STUDENT_NO = "20301"


def _finding_with_pii() -> dict:
    """모든 문자열 필드에 실명이 들어간 검출 항목."""
    return {
        "word": f"{NAME}의 아버지",
        "reason": f"{NAME} 학생의 부모 직업 암시",
        "basis": f"{NAME} 관련 기재요령 위반",
        "severity": f"위반({NAME})",
        "source": f"Gemini 심사 — {NAME}",
        "suggestion_1": f"{NAME}이(가) 배운 점 중심으로 서술",
        "suggestion_2": f"{NAME} 대신 학생의 성장 과정을 서술",
        "count": 3,  # 문자열이 아닌 값
        "extra": None,
    }


def test_mask_findings_masks_every_string_field():
    mask_map = build_mask_map([NAME, STUDENT_NO])
    findings = [_finding_with_pii()]

    masked = mask_findings(findings, mask_map)

    assert len(masked) == 1
    for key, value in masked[0].items():
        if isinstance(value, str):
            assert NAME not in value, f"'{key}' 필드에 실명이 남아 있습니다: {value}"
    # 마스킹 토큰이 실제로 들어갔는지 확인
    assert "《비공개" in masked[0]["word"]
    assert "《비공개" in masked[0]["reason"]
    assert "《비공개" in masked[0]["basis"]
    assert "《비공개" in masked[0]["suggestion_1"]
    assert "《비공개" in masked[0]["suggestion_2"]


def test_mask_findings_preserves_non_string_values():
    mask_map = build_mask_map([NAME])
    masked = mask_findings([_finding_with_pii()], mask_map)
    assert masked[0]["count"] == 3
    assert masked[0]["extra"] is None


def test_mask_findings_does_not_mutate_input():
    mask_map = build_mask_map([NAME, STUDENT_NO])
    findings = [_finding_with_pii(), _finding_with_pii()]
    before = copy.deepcopy(findings)

    masked = mask_findings(findings, mask_map)

    assert findings == before
    assert masked is not findings
    for original, new in zip(findings, masked):
        assert new is not original


def test_mask_findings_handles_empty_map_and_list():
    assert mask_findings([], build_mask_map([NAME])) == []
    findings = [_finding_with_pii()]
    assert mask_findings(findings, []) == findings
