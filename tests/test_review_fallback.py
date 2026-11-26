# -*- coding: utf-8 -*-
"""AI 실패 시 규칙 기반 단독 결과(core.review) + 성능 개선(클라이언트 재사용·중복 규칙 검사 제거·
지연 import·유사도 사전 필터) 회귀 테스트. 실제 Gemini API는 호출하지 않는다."""
import difflib
import json
import os
import subprocess
import sys

import pytest

from core import gemini
from core.review import (
    AI_FAIL_TITLE,
    ai_failure_message,
    build_rules_only_result,
    rules_only_review,
)
from core.rules import _normalize_for_similarity, find_similar_pairs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_KEY = "AQ." + "FAKEx7Kq9" + "z" * 24
TEXT = "TOEIC 900점을 취득하고 구글 클래스룸으로 과제를 제출함."
MULTI = "정보: TOEIC 900점을 취득함.\n물리학: 구글 검색으로 자료를 찾음."


# ── 규칙 기반 단독 결과 ──
def test_rules_only_matches_success_path_rule_part(monkeypatch):
    # 성공 경로에서 Gemini가 아무것도 못 찾았을 때와 규칙 결과가 완전히 같아야 한다.
    monkeypatch.setattr(gemini, "analyze_with_gemini", lambda *a, **k: [])
    mask = [("홍길동", "《비공개1》")]
    text = "홍길동은 " + TEXT
    expected = gemini.review_text_masked(text, "전공", "k", ["클래스룸"], mask)
    assert rules_only_review(text, ["클래스룸"], mask) == expected
    words = {f["word"] for f in expected}
    assert {"TOEIC", "구글", "클래스룸"} <= words
    assert all(f["source"] == "규칙 기반" and f["severity"] == "주의" for f in expected)


def test_build_rules_only_result_single_flags_ai_off():
    r = build_rules_only_result(TEXT, [("", TEXT)], [], [], "오류 문구")
    assert r["ai_ok"] is False and r["ai_error"] == "오류 문구"
    assert r["text"] == TEXT
    assert any(f["word"] == "구글" for f in r["findings"])
    assert all("subject" not in f for f in r["findings"])


def test_build_rules_only_result_multi_tags_subject():
    from core.parsing import split_by_subject

    segs = split_by_subject(MULTI)
    assert len(segs) == 2
    r = build_rules_only_result(MULTI, segs, [], [])
    subj = {f["word"]: f["subject"] for f in r["findings"]}
    assert subj["TOEIC"] == "정보" and subj["구글"] == "물리학"
    assert r["segments"] == segs


def test_rules_only_masks_then_restores_words():
    # 마스킹한 본문으로 검사하고 결과 단어는 원래대로 복원한다.
    r = rules_only_review("홍길동 구글 사용", ["홍길동 구글"], [("홍길동", "《비공개1》")])
    assert "홍길동 구글" not in {f["word"] for f in r}  # 마스킹된 본문엔 없는 금칙어
    assert any(f["word"] == "구글" for f in r)


def test_ai_failure_message_variants():
    msg = ai_failure_message(PermissionError(f"401 UNAUTHENTICATED key={FAKE_KEY}"))
    assert msg.startswith("Gemini API 호출 실패") and FAKE_KEY not in msg
    credit = ai_failure_message(gemini.GeminiCreditError(gemini._CREDIT_MESSAGE))
    assert "크레딧" in credit and "GeminiCreditError" not in credit
    assert "JSON" in ai_failure_message(json.JSONDecodeError("x", "doc", 0))
    assert "키·크레딧" in AI_FAIL_TITLE


# ── B5: review_text 중복 rule_based_filter 제거 ──
def test_review_text_calls_rule_filter_once_without_custom_words(monkeypatch):
    calls = []
    real = gemini.rule_based_filter

    def counting(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(gemini, "rule_based_filter", counting)
    monkeypatch.setattr(gemini, "_make_model", lambda *a, **k: _Stub("[]"))
    gemini.review_text(TEXT, "전공", "k", [])
    assert len(calls) == 1
    calls.clear()
    gemini.review_text(TEXT, "전공", "k", ["클래스룸"])
    assert len(calls) == 2  # 금칙어가 있으면 힌트용 검사를 따로 한다(결과 동일 보장)


def test_analyze_prompt_same_with_precomputed_rules():
    from core.rules import rule_based_filter

    assert gemini.build_analyze_prompts(TEXT, "전공") == gemini.build_analyze_prompts(
        TEXT, "전공", rule_findings=rule_based_filter(TEXT)
    )


class _Resp:
    def __init__(self, text):
        self.text = text


class _Stub:
    def __init__(self, reply):
        self.reply = reply

    def generate_content(self, prompt, model_name=None):
        return _Resp(self.reply)


# ── B1: genai.Client 재사용 ──
def test_client_reused_per_api_key(monkeypatch):
    made = []

    class _C:
        def __init__(self, api_key=None):
            made.append(api_key)
            self.models = None

    monkeypatch.setattr(gemini.genai, "Client", _C)
    gemini.clear_client_cache()
    try:
        a = gemini._make_model(FAKE_KEY, "s", 0.1)
        b = gemini._make_model(FAKE_KEY, "t", 0.2, json_mode=True)
        c = gemini._make_model("other-" + "key-123", "s", 0.1)
        assert a._client is b._client
        assert c._client is not a._client
        assert len(made) == 2
        # 캐시 키에 키 원문이 들어가지 않는다
        assert FAKE_KEY not in repr(list(gemini._CLIENT_CACHE.keys()))
    finally:
        gemini.clear_client_cache()


def test_client_cache_thread_safe(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    made = []

    class _C:
        def __init__(self, api_key=None):
            made.append(1)

    monkeypatch.setattr(gemini.genai, "Client", _C)
    gemini.clear_client_cache()
    try:
        with ThreadPoolExecutor(8) as pool:
            clients = list(pool.map(lambda _: gemini._get_client(FAKE_KEY), range(32)))
        assert len(made) == 1 and len({id(c) for c in clients}) == 1
    finally:
        gemini.clear_client_cache()


# ── B3: 지연 import ──
def test_core_import_does_not_load_heavy_modules():
    code = (
        "import sys, core.gemini, core.parsing, core.formatting, core.report, core.review;"
        "heavy=[m for m in ('google.genai','pandas','openpyxl','olefile') if m in sys.modules];"
        "print(heavy)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": ROOT}, timeout=300,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_lazy_module_proxy_attributes():
    from core.formatting import pd as fpd

    assert fpd.DataFrame({"a": [1]}).shape == (1, 1)
    assert hasattr(gemini.genai_types, "GenerateContentConfig")
    with pytest.raises(AttributeError):
        gemini.no_such_attribute  # noqa: B018


# ── B2: 유사도 사전 필터 — 결과 동일 ──
def _reference_pairs(items, threshold=None):
    from core.rules import SIMILARITY_THRESHOLD

    threshold = SIMILARITY_THRESHOLD if threshold is None else threshold
    pairs = []
    norm = [(n, _normalize_for_similarity(t)) for n, t in items]
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            r = difflib.SequenceMatcher(None, norm[i][1], norm[j][1]).ratio()
            if r >= threshold:
                pairs.append((norm[i][0], norm[j][0], r))
    return sorted(pairs, key=lambda p: -p[2])


def test_find_similar_pairs_equals_reference():
    import random

    rnd = random.Random(7)
    base = "실험을 설계하고 변인을 통제하여 결과를 분석함. 탐구 과정에서 협업 능력이 드러남. " * 4
    words = ["물리", "화학", "생명", "지구", "토론", "발표", "보고서", "그래프", "자료", "가설"]
    items = []
    for k in range(14):
        if k % 3 == 0:
            t = base + " ".join(rnd.choice(words) for _ in range(3))
        elif k % 3 == 1:
            t = base[: len(base) // 2] + " ".join(rnd.choice(words) for _ in range(30))
        else:
            t = " ".join(rnd.choice(words) for _ in range(60))
        items.append((f"학생{k}", t))
    items += [("빈1", ""), ("빈2", ""), ("동일", base)]
    ref = _reference_pairs(items)
    assert ref  # 유사 쌍이 실제로 존재하는 데이터
    assert find_similar_pairs(items) == ref
    for th in (0.3, 0.6, 0.95):
        assert find_similar_pairs(items, th) == _reference_pairs(items, th)
