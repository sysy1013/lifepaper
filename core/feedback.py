# -*- coding: utf-8 -*-
"""건의사항(기능 제안·버그 신고) 이메일 전송.

앱에는 건의 내용을 저장하지 않는다. 운영자 메일함으로 곧바로 보내고 끝낸다.
Streamlit에 의존하지 않는 순수 로직 + 표준 라이브러리 SMTP만 사용한다.
"""

import smtplib
from datetime import datetime
from email.message import EmailMessage

# 건의 메일을 받을 기본 주소. secrets의 FEEDBACK_TO로 덮어쓸 수 있다.
# (수신 주소는 비밀정보가 아니므로 코드에 두고, 발송 계정 정보만 secrets로 받는다.)
DEFAULT_FEEDBACK_TO = "sihyeongs73@gmail.com"

# 이메일 전송에 반드시 필요한 secrets 키
# (FEEDBACK_TO는 기본값이 있고, SMTP_PORT도 기본값 465가 있어 선택 항목)
FEEDBACK_SECRET_KEYS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")

DEFAULT_SMTP_PORT = 465


def feedback_recipient(secrets) -> str:
    """건의 메일 수신 주소. secrets에 값이 있으면 그것을, 없으면 기본 주소를 쓴다."""
    try:
        configured = str(secrets.get("FEEDBACK_TO", "") or "").strip()
    except Exception:
        configured = ""
    return configured or DEFAULT_FEEDBACK_TO


def is_configured(secrets) -> bool:
    """이메일 전송에 필요한 설정이 모두 있는지 확인한다."""
    for key in FEEDBACK_SECRET_KEYS:
        try:
            value = secrets.get(key, "")
        except Exception:
            return False
        if not str(value).strip():
            return False
    return True


def build_feedback_message(kind: str, body: str) -> tuple[str, str]:
    """(제목, 본문) 튜플을 만든다.

    본문에는 유형·작성 시각(ISO)·내용을 담는다.
    """
    kind = (kind or "기타").strip() or "기타"
    subject = f"[생기부 도우미] {kind}"
    text = (
        f"유형: {kind}\n"
        f"작성 시각: {datetime.now().isoformat(timespec='seconds')}\n"
        "----------------------------------------\n"
        f"{(body or '').strip()}\n"
    )
    return subject, text


def send_feedback_email(kind: str, body: str, secrets) -> None:
    """SMTP(SSL)로 건의 메일을 보낸다. 실패하면 예외를 그대로 올린다."""
    host = str(secrets.get("SMTP_HOST", "")).strip()
    user = str(secrets.get("SMTP_USER", "")).strip()
    password = str(secrets.get("SMTP_PASSWORD", ""))
    to_addr = feedback_recipient(secrets)
    try:
        port = int(secrets.get("SMTP_PORT", DEFAULT_SMTP_PORT) or DEFAULT_SMTP_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_SMTP_PORT

    subject, text = build_feedback_message(kind, body)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.set_content(text, charset="utf-8")

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(user, password)
        server.send_message(msg)
