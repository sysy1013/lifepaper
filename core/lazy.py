# -*- coding: utf-8 -*-
"""무거운 모듈(pandas 등)을 처음 쓸 때 불러오는 지연 import 프록시.

첫 화면에 필요 없는 라이브러리 import 비용(수 초)을 실제 사용 시점으로 미룬다.
`pd = lazy_module("pandas")` 후 `pd.DataFrame(...)`처럼 그대로 쓰면 된다.
"""

import importlib
import threading


class _LazyModule:
    __slots__ = ("_name", "_mod", "_lock")

    def __init__(self, name: str):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_mod", None)
        object.__setattr__(self, "_lock", threading.Lock())

    def _load(self):
        mod = self._mod
        if mod is None:
            with self._lock:
                mod = self._mod
                if mod is None:
                    mod = importlib.import_module(self._name)
                    object.__setattr__(self, "_mod", mod)
        return mod

    def __getattr__(self, attr: str):
        return getattr(self._load(), attr)

    def __setattr__(self, attr: str, value) -> None:  # monkeypatch 등은 실제 모듈에 반영
        setattr(self._load(), attr, value)

    def __delattr__(self, attr: str) -> None:
        delattr(self._load(), attr)

    def __repr__(self) -> str:
        state = "loaded" if self._mod is not None else "not loaded"
        return f"<lazy module {self._name!r} ({state})>"


def lazy_module(name: str):
    """name 모듈을 처음 속성에 접근할 때 import하는 프록시를 돌려준다."""
    return _LazyModule(name)
