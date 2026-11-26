# -*- coding: utf-8 -*-
"""키·개인정보 유출 방지 하드닝 테스트 (core/security.py, app.py 오류 표시, 저장소·디스크 정적 검사)."""

import os
import re
import shutil
import subprocess

import pytest

from core.security import (
    REDACTED,
    clear_registered_secrets,
    redact_secrets,
    register_secret,
    safe_error,
    sanitize_exception,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 저장소에 키 형태 리터럴을 남기지 않도록 실행 시 조립한다.
# 가짜 값(스캐너 오탐 방지: 조각으로 조립)
_K1 = "abc" + "DEF" + "123456"
_K2 = "k3y" + "-VAL" + "UE-998877"
_K3 = "eyJh" + "bGci" + "OiJI" + ".pay" + "load"
_K4 = "super" + "secret" + "999"
FAKE_AQ = "AQ." + "FAKE" + "x7Kq9TestOnlyToken_zz"
FAKE_AIZA = "AIza" + "SyFAKE0123456789abcdefghijklmnopq"


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_registered_secrets()
    yield
    clear_registered_secrets()


# ── (b) redact_secrets 패턴 ──
@pytest.mark.parametrize(
    "raw, secret",
    [
        (f"400 API key not valid: {FAKE_AIZA}", FAKE_AIZA),
        (f"token {FAKE_AQ} expired", FAKE_AQ),
        (f"GET https://x.googleapis.com/v1/models?key={_K1}&alt=json", _K1),
        (f"headers: {{'x-goog-api-key': '{_K2}'}}", _K2),
        (f"Authorization: Bearer {_K3}", _K3),
        (f"api_key={_K4}", _K4),
    ],
)
def test_redact_patterns(raw, secret):
    out = redact_secrets(raw)
    assert secret not in out
    assert REDACTED in out


def test_redact_registered_and_extra_values():
    register_secret("my-app-password-77")
    out = redact_secrets("pw my-app-password-77 and other-secret-55", extra=("other-secret-55",))
    assert "my-app-password-77" not in out and "other-secret-55" not in out


@pytest.mark.parametrize(
    "text",
    [
        "Gemini가 빈 응답을 반환했습니다.",
        "429 rate limit exceeded",
        "학생이 실험 결과를 분석함. 키워드 정리.",
        "AI 도구와 PPT를 활용함",
    ],
)
def test_redact_leaves_normal_text(text):
    assert redact_secrets(text) == text


def test_register_ignores_short_values():
    register_secret("ab")
    assert redact_secrets("ab abc") == "ab abc"


def test_safe_error_type_mask_and_length():
    register_secret(FAKE_AQ)
    e = RuntimeError(f"failed with {FAKE_AQ} " + "본문" * 400)
    s = safe_error(e)
    assert s.startswith("RuntimeError: ")
    assert FAKE_AQ not in s
    assert len(s) <= len("RuntimeError: ") + 301
    assert safe_error(ValueError("형식 오류"), with_type=False) == "형식 오류"


def test_sanitize_exception_keeps_type():
    e = PermissionError(f"403 denied key={FAKE_AIZA}")
    out = sanitize_exception(e)
    assert out is e and isinstance(out, PermissionError)
    assert FAKE_AIZA not in str(out)
    plain = ValueError("429 rate limit")
    assert str(sanitize_exception(plain)) == "429 rate limit"


def test_gemini_text_reraise_is_sanitized(monkeypatch):
    from core import gemini

    monkeypatch.setattr("core.gemini.time.sleep", lambda *a, **k: None)

    class _M:
        def generate_content(self, prompt, model_name=None):
            raise PermissionError(f"403 PERMISSION_DENIED for {FAKE_AIZA}")

    with pytest.raises(PermissionError) as ei:
        gemini._gemini_text(_M(), "p")
    assert FAKE_AIZA not in str(ei.value)


# ── (a) 화면 어디에도 키가 나오지 않음 (AppTest) ──
def _all_text(at) -> str:
    chunks = []

    def walk(node):
        proto = getattr(node, "proto", None)
        if proto is not None:
            chunks.append(str(proto))
        children = getattr(node, "children", None)
        if isinstance(children, dict):
            for c in children.values():
                walk(c)

    walk(at._tree)
    return "\n".join(chunks)


def test_app_never_shows_api_key_on_gemini_error(monkeypatch):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    from core import gemini

    # 주의: time.sleep을 패치하면 AppTest 대기 루프가 바쁜 대기가 되어 시간 초과가 난다.
    # 403은 재시도 대상이 아니므로 대기가 생기지 않는다.

    class _Models:
        def generate_content(self, model=None, contents=None, config=None):
            raise PermissionError(
                f"403 PERMISSION_DENIED. url=https://x/v1beta?key={FAKE_AQ} api key {FAKE_AQ}"
            )

    class _Client:
        def __init__(self, *a, **k):
            self.models = _Models()

    monkeypatch.setattr(gemini.genai, "Client", _Client)

    old = os.getcwd()
    os.chdir(ROOT)
    try:
        at = AppTest.from_file("app.py", default_timeout=60)
        at.secrets["GEMINI_API_KEY"] = FAKE_AQ
        at.run()
        method = next(r for r in at.radio if "직접 붙여넣기" in r.options)
        method.set_value("직접 붙여넣기").run()
        at.text_area(key="input_text").input("실험 결과를 분석해 그래프로 정리함.").run()
        btn = next(b for b in at.button if "검토 실행" in str(b.label))
        btn.click().run()
    finally:
        os.chdir(old)
    screen = _all_text(at)
    # AI 실패는 규칙 기반 결과와 함께 경고 콜아웃으로 안내한다(core.review.AI_FAIL_TITLE).
    from core.review import AI_FAIL_TITLE

    assert at.error or any(AI_FAIL_TITLE in str(w.value) for w in at.warning), (
        "오류 안내가 표시되어야 함"
    )
    assert FAKE_AQ not in screen
    assert "FAKEx7Kq9" not in screen


def test_clear_all_removes_image_state():
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    old = os.getcwd()
    os.chdir(ROOT)
    try:
        at = AppTest.from_file("app.py", default_timeout=60)
        # 로컬 secrets.toml(접근 암호 등)을 읽지 않도록 테스트용 secrets만 쓴다
        at.secrets["GEMINI_API_KEY"] = FAKE_AQ
        at.run()
        at.session_state["image_transcript"] = "국어: 전사본"
        at.session_state["image_previews"] = [("a.png", b"\x89PNG")]
        at.session_state["image_verify"] = [{"transcript": "a", "image": "b", "note": ""}]
        at.button(key="clear_all").click().run()
    finally:
        os.chdir(old)
    leftover = [k for k in at.session_state.filtered_state if str(k).startswith("image_")]
    assert leftover == []


# ── (c) 저장소에 키가 커밋되지 않음 ──
_KEY_RES = (
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"\bAQ\.[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}"),
)


def _git_files():
    if shutil.which("git") is None:
        pytest.skip("git 없음")
    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    except Exception:
        pytest.skip("git 저장소 아님")
    return tracked + untracked


def test_no_secrets_in_repo_files():
    hits = []
    for rel in _git_files():
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path) or os.path.getsize(path) > 2_000_000:
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        for rx in _KEY_RES:
            if rx.search(text):
                hits.append(rel)
    assert not hits, f"키 형태 문자열이 저장소 파일에 있음: {hits}"


def test_secrets_toml_is_ignored():
    if shutil.which("git") is None:
        pytest.skip("git 없음")
    r = subprocess.run(
        ["git", "check-ignore", ".streamlit/secrets.toml", "x.key", ".env.local"],
        cwd=ROOT, capture_output=True, text=True,
    )
    ignored = set(r.stdout.split())
    assert {".streamlit/secrets.toml", "x.key", ".env.local"} <= ignored


# ── (d) 업로드 이미지·전사본·본문을 디스크에 쓰지 않음 (정적 검사) ──
_WRITE_RES = (
    re.compile(r"open\([^)]*['\"][wax]b?\+?['\"]"),
    re.compile(r"\btempfile\b|NamedTemporaryFile|mkstemp|mkdtemp"),
    re.compile(r"\.write_bytes\(|\.write_text\(|\.to_file\("),
    re.compile(r"\.to_(?:csv|excel|json|parquet|pickle)\(\s*['\"f]"),
    re.compile(r"\.save\(\s*['\"f]"),
    re.compile(r"shelve|pickle\.dump\("),
)


def test_app_and_core_do_not_write_files():
    targets = [os.path.join(ROOT, "app.py")] + [
        os.path.join(ROOT, "core", n)
        for n in os.listdir(os.path.join(ROOT, "core"))
        if n.endswith(".py")
    ]
    hits = []
    for path in targets:
        for ln, line in enumerate(open(path, encoding="utf-8"), 1):
            code = line.split("#", 1)[0]
            for rx in _WRITE_RES:
                if rx.search(code):
                    hits.append(f"{os.path.basename(path)}:{ln}")
    assert not hits, f"디스크 쓰기 의심 호출: {hits}"
