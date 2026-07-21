# -*- coding: utf-8 -*-
"""되돌리기(undo) 스택 로직 (Streamlit 비의존 · 매핑 객체를 받아 동작).

세션 상태처럼 동작하는 임의의 MutableMapping(store)을 받아
`__undo__{state_key}` 키에 이전 값 목록(오래된 것 → 최신 순)을 보관한다.
최대 UNDO_DEPTH개까지만 유지하며, 넘치면 가장 오래된 값부터 버린다.
"""

UNDO_DEPTH = 3


def undo_stack_key(state_key: str) -> str:
    """state_key에 대응하는 되돌리기 스택 저장 키."""
    return f"__undo__{state_key}"


def push_undo(store, state_key: str, value) -> None:
    """현재 값을 되돌리기 스택에 넣는다 (최대 UNDO_DEPTH개, 최신이 마지막)."""
    sk = undo_stack_key(state_key)
    stack = list(store.get(sk) or [])
    stack.append(value)
    del stack[:-UNDO_DEPTH]
    store[sk] = stack


def pop_undo(store, state_key: str):
    """가장 최근 값을 꺼내 반환한다. 없으면 None."""
    sk = undo_stack_key(state_key)
    stack = list(store.get(sk) or [])
    if not stack:
        return None
    value = stack.pop()
    if stack:
        store[sk] = stack
    else:
        store.pop(sk, None)
    return value


def has_undo(store, state_key: str) -> bool:
    """되돌릴 이전 값이 하나라도 있으면 True."""
    return bool(store.get(undo_stack_key(state_key)))
