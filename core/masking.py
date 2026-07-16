# -*- coding: utf-8 -*-
"""개인정보 마스킹 (Gemini API 전송 전 치환, 결과에서 복원)."""


def build_mask_map(mask_words: list[str]) -> list[tuple[str, str]]:
    """마스킹 단어 → 치환 토큰 쌍 목록을 만든다. 긴 단어부터 치환되도록 정렬."""
    uniq = sorted({w.strip() for w in mask_words if w.strip()}, key=len, reverse=True)
    return [(w, f"《비공개{i + 1}》") for i, w in enumerate(uniq)]


def apply_mask(text: str, mask_map: list[tuple[str, str]]) -> str:
    """API 전송 전 개인정보 단어를 토큰으로 치환한다."""
    for word, token in mask_map:
        text = text.replace(word, token)
    return text


def remove_mask(text: str, mask_map: list[tuple[str, str]]) -> str:
    """API 응답에 남은 토큰을 원래 단어로 복원한다."""
    for word, token in mask_map:
        text = text.replace(token, word)
    return text
