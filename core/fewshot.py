# -*- coding: utf-8 -*-
"""Gemini 기재 점검용 few-shot 예시 풀과 선택 로직 (외부 API 불필요, 순수 로직).

예시(core/fewshot_examples.json)는 이 앱을 위해 새로 지은 합성 세특 문장이다(가상 이름).
골든셋·홀드아웃 평가 문장과 겹치지 않도록 tests/test_fewshot_isolation.py가 검사한다.
"""

import json
import os
import re
from functools import lru_cache

from core.rulebook import RULEBOOK

_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fewshot_examples.json")
VERDICTS = ("기재 불가", "확인 필요", "기재 가능")
REQUIRED_KEYS = ("category", "text", "verdict", "word", "reason", "basis", "exception_note")


@lru_cache(maxsize=1)
def load_examples() -> tuple[dict, ...]:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return tuple(json.load(f))


def _ngrams(text: str, n: int) -> set[str]:
    s = re.sub(r"\s+", "", text or "")
    return {s[i : i + n] for i in range(max(0, len(s) - n + 1))}


def similarity(a: str, b: str) -> float:
    """문자 2-gram·3-gram 자카드 유사도의 평균 (0~1)."""
    scores = []
    for n in (2, 3):
        x, y = _ngrams(a, n), _ngrams(b, n)
        if x or y:
            scores.append(len(x & y) / len(x | y))
    return sum(scores) / len(scores) if scores else 0.0


def select_fewshots(text: str, k: int = 6, hint_categories=()) -> list[dict]:
    """입력 문장과 비슷한 예시를 범주가 겹치지 않게 k개 고른다.

    - 점수 = 문자 n-gram 유사도 (+ 규칙 기반 검출 범주와 같으면 가산)
    - 1차: 범주당 1개씩 점수순, 부족하면 2차로 남은 예시를 점수순 보충
    - k>=3이면 '기재 가능' 예시를 최소 1개 포함해 과탐지를 견제한다
    """
    if k <= 0:
        return []
    hints = set(hint_categories or ())
    examples = load_examples()
    scored = sorted(
        examples,
        key=lambda e: -(similarity(text, e["text"]) + (0.3 if e["category"] in hints else 0.0)),
    )
    chosen: list[dict] = []
    used_cats: set[str] = set()
    for e in scored:
        if len(chosen) >= k:
            break
        if e["category"] not in used_cats:
            chosen.append(e)
            used_cats.add(e["category"])
    for e in scored:
        if len(chosen) >= k:
            break
        if e not in chosen:
            chosen.append(e)
    if k >= 3 and not any(e["verdict"] == "기재 가능" for e in chosen):
        allowed = next((e for e in scored if e["verdict"] == "기재 가능"), None)
        if allowed is not None:
            chosen[-1] = allowed
    return chosen


def format_fewshot_block(examples: list[dict]) -> str:
    """프롬프트 주입용 예시 블록 문자열."""
    lines = ["[판단 참고 예시 — 문체·판단 기준만 참고하고 예시의 내용은 결과에 넣지 않는다]"]
    for i, e in enumerate(examples, 1):
        item = {
            "word": e["word"],
            "verdict": e["verdict"],
            "reason": e["reason"],
            "cited_rule": e["category"],
        }
        if e.get("exception_note"):
            item["exception_note"] = e["exception_note"]
        lines.append(f"예시 {i}) 문장: {e['text']}")
        lines.append("   판단: " + json.dumps(item, ensure_ascii=False))
    return "\n".join(lines)


# 기재금지 '내용' 점검 대상 rule_id (문체·서식 항목 제외) — 조항 발췌 범위
CONTENT_RULE_IDS = (
    "brand", "competition", "outside_award", "university_name", "institution", "lecturer",
    "parent_status", "overseas", "paper_book", "ip_right", "scholarship", "language_test",
    "certification_test", "mock_exam", "score", "license", "school_name", "mooc_afterschool",
    "thesis", "thesis_record", "youth_org", "gifted_edu",
)


def rulebook_excerpt(rule_ids=None, max_chars: int = 2400) -> str:
    """기재요령 조항 요약 발췌(rulebook)를 max_chars 이내로 만든다."""
    ids = [r for r in (rule_ids or CONTENT_RULE_IDS) if r in RULEBOOK]
    lines = ["[기재요령 조항 발췌 — cited_rule에는 아래 rule_id 중 하나를 그대로 쓴다]"]
    total = len(lines[0])
    for rid in ids:
        e = RULEBOOK[rid]
        line = f"- {rid}: {e['title']} — {e['summary']}"
        if e.get("exceptions"):
            line += f" (예외: {e['exceptions']})"
        line += f" [근거: {e['source']}]"
        if total + len(line) + 1 > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


_RULE_ID_FORMAT = re.compile(r"^[a-z][a-z_]*$")
_TITLE_TO_ID = {v["title"]: k for k, v in RULEBOOK.items()}


def resolve_cited_rule(cited) -> tuple[str | None, str]:
    """cited_rule 값을 검증한다. (rule_id 또는 None, 상태) — 상태: ok / missing / malformed / unknown."""
    if cited is None or (isinstance(cited, str) and not cited.strip()):
        return None, "missing"
    if not isinstance(cited, str):
        return None, "malformed"
    c = cited.strip()
    if c in RULEBOOK:
        return c, "ok"
    if c in _TITLE_TO_ID:
        return _TITLE_TO_ID[c], "ok"
    if not _RULE_ID_FORMAT.match(c):
        return None, "malformed"
    return None, "unknown"
