# -*- coding: utf-8 -*-
from core.masking import apply_mask, build_mask_map, remove_mask


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
