# -*- coding: utf-8 -*-
from core.masking import (
    apply_mask,
    build_mask_map,
    remove_mask,
    suggest_mask_candidates,
)


def test_build_mask_map_orders_longer_words_first():
    # 짧은 단어가 긴 단어의 부분 문자열일 때, 긴 단어부터 치환되어야 한다.
    mask_map = build_mask_map(["홍", "홍길동"])
    words = [w for w, _ in mask_map]
    assert words == ["홍길동", "홍"]
    # 토큰은 순서대로 부여된다.
    assert mask_map[0][1] == "《비공개1》"
    assert mask_map[1][1] == "《비공개2》"


def test_build_mask_map_dedupes_and_strips():
    mask_map = build_mask_map(["  홍길동  ", "홍길동", "", "   "])
    assert len(mask_map) == 1
    assert mask_map[0][0] == "홍길동"


def test_apply_remove_roundtrip():
    mask_map = build_mask_map(["홍길동", "20301"])
    text = "홍길동 학생(20301)은 성실하다."
    masked = apply_mask(text, mask_map)
    assert "홍길동" not in masked
    assert "20301" not in masked
    assert remove_mask(masked, mask_map) == text


def test_empty_map_is_noop():
    text = "아무 내용"
    assert apply_mask(text, []) == text
    assert remove_mask(text, []) == text


# ── suggest_mask_candidates ──
def test_suggest_detects_repeated_name():
    texts = ["김철수 학생은 성실함.", "발표에서 김철수 학생이 탐구 과정을 설명함."]
    assert "김철수" in suggest_mask_candidates(texts)


def test_suggest_detects_student_number():
    assert "20301" in suggest_mask_candidates(["학번 20301 학생의 활동 기록"])


def test_suggest_excludes_stopword():
    # '이해'는 성씨로 시작하지만 흔한 단어 → 불용어로 제외
    texts = ["내용을 이해함.", "깊이 이해하는 모습을 이해함."]
    assert "이해" not in suggest_mask_candidates(texts)


def test_suggest_excludes_existing_entries():
    texts = ["김철수 학생.", "김철수 발표함."]
    assert "김철수" not in suggest_mask_candidates(texts, existing=["김철수"])


def test_suggest_respects_max_5():
    names = ["김철수", "이영희", "박민수", "최지훈", "정하늘", "강도현", "조은별"]
    texts = [" ".join(names), " ".join(names)]  # 각 이름 2회 등장
    assert len(suggest_mask_candidates(texts)) <= 5


def test_suggest_single_occurrence_name_not_suggested():
    # 한 번만 등장하고 "학생"도 뒤따르지 않는 이름은 제안하지 않는다 (오탐 감소).
    assert "김철수" not in suggest_mask_candidates(["김철수 발표함."])


def test_suggest_single_occurrence_name_followed_by_student_is_suggested():
    # 1회만 등장해도 "학생"이 바로 뒤에 이어지면(공백 포함) 제안한다.
    texts = ["김철수 학생은 성실함.", "이영희 학생이 발표를 진행함."]
    result = suggest_mask_candidates(texts)
    assert "김철수" in result
    assert "이영희" in result
