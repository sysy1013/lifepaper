# -*- coding: utf-8 -*-
"""few-shot 예시 풀이 골든셋·홀드아웃 평가 문장과 겹치지 않는지 검사한다(평가 누수 방지).

실패 메시지에는 평가 문장을 출력하지 않는다(케이스 id와 예시 번호만).
"""

import difflib
import json
import os
import re

import pytest

from core.fewshot import REQUIRED_KEYS, VERDICTS, load_examples

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_FILES = [
    os.path.join(ROOT, "tests", "golden", "seteuk_cases.jsonl"),
    os.path.join(ROOT, "tests", "golden", "holdout_cases.jsonl"),
]
MAX_RATIO = 0.6


def _eval_cases():
    cases = []
    for path in EVAL_FILES:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    c = json.loads(line)
                    cases.append((c["id"], c["text"]))
    return cases


def _sentences(text):
    return [s.strip() for s in re.split(r"[.!?\n]+", text) if len(s.strip()) >= 8]


def _norm(s):
    return re.sub(r"\s+", "", s)


def test_eval_files_present():
    cases = _eval_cases()
    if not cases:
        pytest.skip("평가 파일 없음")
    assert len(cases) >= 90


def test_fewshot_not_similar_to_eval_cases():
    cases = _eval_cases()
    if not cases:
        pytest.skip("평가 파일 없음")
    bad = []
    for i, ex in enumerate(load_examples()):
        for cid, text in cases:
            r = difflib.SequenceMatcher(None, ex["text"], text).ratio()
            if r >= MAX_RATIO:
                bad.append((i, cid, round(r, 2)))
    assert not bad, f"평가 문장과 유사한 few-shot 예시(예시번호, 케이스id, ratio): {bad}"


def test_fewshot_no_literal_sentence_overlap():
    cases = _eval_cases()
    if not cases:
        pytest.skip("평가 파일 없음")
    eval_sents = {(cid, _norm(s)) for cid, t in cases for s in _sentences(t)}
    bad = []
    for i, ex in enumerate(load_examples()):
        for s in _sentences(ex["text"]):
            ns = _norm(s)
            for cid, es in eval_sents:
                if ns == es or ns in es or es in ns:
                    bad.append((i, cid))
    assert not bad, f"문장 리터럴 일치(예시번호, 케이스id): {bad}"


def test_fewshot_schema():
    examples = load_examples()
    assert 40 <= len(examples) <= 60
    for i, ex in enumerate(examples):
        for k in REQUIRED_KEYS:
            assert k in ex, (i, k)
        assert ex["verdict"] in VERDICTS, i
        assert ex["word"] and ex["word"] in ex["text"], i
    per_cat = {}
    for ex in examples:
        per_cat[ex["category"]] = per_cat.get(ex["category"], 0) + 1
    assert max(per_cat.values()) <= 4
    assert {ex["verdict"] for ex in examples} == set(VERDICTS)
