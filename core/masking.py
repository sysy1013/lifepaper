# -*- coding: utf-8 -*-
"""개인정보 마스킹 (Gemini API 전송 전 치환, 결과에서 복원)."""

import re
from collections import Counter

# 마스킹 자동 제안에 쓰는 대표 성씨 (~40자)
_SURNAMES = "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민"

# 성씨로 시작하지만 이름이 아닐 가능성이 높은 흔한 단어 (오탐 제외용)
_NAME_STOPWORDS = {
    "김치", "이해", "이상", "이후", "이내", "이번", "박수", "정도", "정리",
    "조사", "주제", "안내", "문제", "문장", "성적", "성장", "신청", "권장",
    "장점", "고민", "최고", "최선",
}

# 성씨 + 1~2글자, 한글 경계로 둘러싸인 독립 단어
_NAME_PATTERN = re.compile(rf"(?<![가-힣])[{_SURNAMES}][가-힣]{{1,2}}(?![가-힣])")
# 5자리 학번
_STUDENT_NO_PATTERN = re.compile(r"\b\d{5}\b")


def suggest_mask_candidates(
    texts: list[str], existing: list[str] | None = None
) -> list[str]:
    """생기부 텍스트에서 개인정보로 보이는 표현(이름·학번)을 자동 추출한다.

    - 이름 후보: 성씨 + 1~2 한글, 독립 단어로 등장하며 전체에서 2회 이상 반복.
    - 학번 후보: 5자리 숫자.
    빈도 높은 순으로 최대 5개, 중복·기존 등록어·불용어는 제외한다.
    """
    existing = existing or []
    name_counts: Counter = Counter()
    number_counts: Counter = Counter()
    for text in texts:
        if not text:
            continue
        for m in _NAME_PATTERN.findall(text):
            if m not in _NAME_STOPWORDS:
                name_counts[m] += 1
        for m in _STUDENT_NO_PATTERN.findall(text):
            number_counts[m] += 1

    def _excluded(word: str) -> bool:
        # 기존 등록어와 포함 관계면 제외 (부분/상위 문자열 모두)
        for e in existing:
            if not e:
                continue
            if word == e or word in e or e in word:
                return True
        return False

    candidates: list[tuple[str, int]] = []
    # 이름은 2회 이상 반복된 것만 (실제 생기부에서 이름은 반복 → 오탐 감소)
    for word, cnt in name_counts.items():
        if cnt >= 2 and not _excluded(word):
            candidates.append((word, cnt))
    # 학번은 1회라도 등장하면 후보
    for word, cnt in number_counts.items():
        if not _excluded(word):
            candidates.append((word, cnt))

    candidates.sort(key=lambda x: -x[1])
    return [w for w, _ in candidates][:5]


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
