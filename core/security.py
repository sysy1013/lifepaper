# -*- coding: utf-8 -*-
"""비밀값(API 키·접근 암호) 유출 방지 — 화면·로그에 내보내는 문자열을 마스킹한다.

- redact_secrets(text, extra=()): 키 형태 패턴과 등록된 비밀값을 '‹비공개›'로 치환
- safe_error(e): 예외를 '유형: 마스킹된 메시지(≤300자)'로 바꿔 화면 표시용으로 쓴다.
  예외 메시지에 프롬프트·학생 본문이 실려 와도 길이 제한으로 노출을 줄인다.
"""

import re

REDACTED = "‹비공개›"
MAX_ERROR_CHARS = 300

# 등록된 비밀값(현재 API 키, APP_PASSWORD 등) — 앱 시작 시 register_secret으로 넣는다.
_REGISTERED: set[str] = set()

_SECRET_PATTERNS = (
    # Google API 키 (AIza + 35자 내외)
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    # Google OAuth/액세스 토큰류 (AQ. / ya29. 로 시작)
    re.compile(r"\bAQ\.[0-9A-Za-z_\-\.]{8,}"),
    re.compile(r"\bya29\.[0-9A-Za-z_\-\.]{8,}"),
    # Authorization: Bearer …
    re.compile(r"(?i)(bearer\s+)[0-9A-Za-z_\-\.=+/]{8,}"),
    # x-goog-api-key: … / "x-goog-api-key": "…"
    re.compile(r"(?i)(x-goog-api-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&]+"),
    # URL·설정의 key=… / api_key=… / apikey: …
    re.compile(r"(?i)((?:[?&\s]|^)(?:api[_-]?key|key|token|password|app_password)\s*[=:]\s*[\"']?)[^\s\"',;&]+"),
)


def register_secret(value) -> None:
    """마스킹할 비밀값을 등록한다(짧은 값은 오탐 방지를 위해 무시)."""
    if isinstance(value, str) and len(value.strip()) >= 6:
        _REGISTERED.add(value.strip())


def clear_registered_secrets() -> None:
    _REGISTERED.clear()


def redact_secrets(text, extra=()) -> str:
    """text 안의 키 형태 패턴·등록 비밀값·extra 값을 '‹비공개›'로 치환한다."""
    s = "" if text is None else str(text)
    values = sorted(
        {v.strip() for v in list(_REGISTERED) + [x for x in extra if isinstance(x, str)] if v and len(v.strip()) >= 6},
        key=len,
        reverse=True,
    )
    for v in values:
        s = s.replace(v, REDACTED)
    for rx in _SECRET_PATTERNS:
        if rx.groups:
            s = rx.sub(lambda m: m.group(1) + REDACTED, s)
        else:
            s = rx.sub(REDACTED, s)
    return s


def safe_error(e: BaseException, with_type: bool = True, extra=()) -> str:
    """예외를 화면 표시용 문자열로: '유형: 마스킹된 메시지'(최대 300자)."""
    msg = redact_secrets(str(e), extra)
    msg = re.sub(r"\s+", " ", msg).strip()
    if len(msg) > MAX_ERROR_CHARS:
        msg = msg[:MAX_ERROR_CHARS] + "…"
    if not with_type:
        return msg
    name = type(e).__name__
    return f"{name}: {msg}" if msg else name


def sanitize_exception(e: BaseException) -> BaseException:
    """예외 객체의 메시지에 비밀값이 있으면 같은 객체의 args·message를 마스킹해 돌려준다.

    예외 유형·속성은 그대로 두어 재시도·폴백 분기(유형·문구 판정)에 영향을 주지 않는다.
    """
    try:
        original = str(e)
        cleaned = redact_secrets(original)
        if cleaned != original:
            e.args = (cleaned,)
            if isinstance(getattr(e, "message", None), str):
                e.message = redact_secrets(e.message)
    except Exception:
        pass
    return e
