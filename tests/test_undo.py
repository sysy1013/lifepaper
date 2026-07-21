# -*- coding: utf-8 -*-
"""되돌리기(undo) 스택 로직 테스트."""

from core.undo import UNDO_DEPTH, has_undo, pop_undo, push_undo, undo_stack_key


def test_push_then_pop_returns_value_and_empties_stack():
    store = {}
    push_undo(store, "draft_text", "원래 초안")
    assert pop_undo(store, "draft_text") == "원래 초안"
    assert has_undo(store, "draft_text") is False
    assert undo_stack_key("draft_text") not in store


def test_depth_cap_keeps_only_newest_three():
    store = {}
    for i in range(5):
        push_undo(store, "draft_text", f"v{i}")
    assert store[undo_stack_key("draft_text")] == ["v2", "v3", "v4"]
    assert len(store[undo_stack_key("draft_text")]) == UNDO_DEPTH
    assert pop_undo(store, "draft_text") == "v4"
    assert pop_undo(store, "draft_text") == "v3"
    assert pop_undo(store, "draft_text") == "v2"
    assert pop_undo(store, "draft_text") is None


def test_has_undo_lifecycle():
    store = {}
    assert has_undo(store, "revised_text") is False
    push_undo(store, "revised_text", "a")
    push_undo(store, "revised_text", "b")
    assert has_undo(store, "revised_text") is True
    pop_undo(store, "revised_text")
    assert has_undo(store, "revised_text") is True
    pop_undo(store, "revised_text")
    assert has_undo(store, "revised_text") is False


def test_pop_empty_stack_returns_none():
    store = {}
    assert pop_undo(store, "없는키") is None


def test_stacks_are_independent_per_key():
    store = {}
    push_undo(store, "draft_text", "d1")
    push_undo(store, "revised_text", "r1")
    assert pop_undo(store, "draft_text") == "d1"
    assert has_undo(store, "revised_text") is True


def test_dict_value_snapshot_is_supported():
    store = {}
    original = {"text": "본문", "findings": [{"word": "우수"}]}
    push_undo(store, "review_result", dict(original))
    original["text"] = "바뀐 본문"
    restored = pop_undo(store, "review_result")
    assert restored["text"] == "본문"
