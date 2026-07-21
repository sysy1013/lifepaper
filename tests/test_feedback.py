# -*- coding: utf-8 -*-
"""건의사항 이메일 전송 로직 테스트 (실제 네트워크 사용 안 함)."""

import smtplib

import pytest

from core.feedback import (
    FEEDBACK_SECRET_KEYS,
    build_feedback_message,
    is_configured,
    send_feedback_email,
)

FULL_SECRETS = {
    "FEEDBACK_TO": "operator@example.com",
    "SMTP_HOST": "smtp.example.com",
    "SMTP_PORT": 465,
    "SMTP_USER": "sender@example.com",
    "SMTP_PASSWORD": "app-password",
}


# ── is_configured ──
def test_is_configured_true_when_all_keys_present():
    assert is_configured(FULL_SECRETS) is True


def test_is_configured_true_without_optional_port():
    secrets = {k: v for k, v in FULL_SECRETS.items() if k != "SMTP_PORT"}
    assert is_configured(secrets) is True


@pytest.mark.parametrize("missing", FEEDBACK_SECRET_KEYS)
def test_is_configured_false_when_key_missing(missing):
    secrets = {k: v for k, v in FULL_SECRETS.items() if k != missing}
    assert is_configured(secrets) is False


@pytest.mark.parametrize("blank", FEEDBACK_SECRET_KEYS)
def test_is_configured_false_when_key_blank(blank):
    secrets = dict(FULL_SECRETS)
    secrets[blank] = "   "
    assert is_configured(secrets) is False


def test_is_configured_false_for_empty_secrets():
    assert is_configured({}) is False


# ── build_feedback_message ──
def test_build_feedback_message_title_and_body():
    subject, body = build_feedback_message("버그 신고", "일괄 검토에서 오류가 납니다.")
    assert subject == "[생기부 도우미] 버그 신고"
    assert "버그 신고" in body
    assert "일괄 검토에서 오류가 납니다." in body
    assert "작성 시각" in body


def test_build_feedback_message_defaults_kind_when_blank():
    subject, body = build_feedback_message("  ", "이런 기능이 있으면 좋겠습니다.")
    assert subject == "[생기부 도우미] 기타"
    assert "유형: 기타" in body


def test_build_feedback_message_iso_timestamp():
    import re

    _, body = build_feedback_message("기타", "내용")
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", body)


# ── send_feedback_email ──
class FakeSMTP:
    """smtplib.SMTP_SSL 대역 — 호출 내용을 기록만 한다."""

    instances: list["FakeSMTP"] = []

    def __init__(self, host, port=0, *args, **kwargs):
        self.host = host
        self.port = port
        self.logins: list[tuple[str, str]] = []
        self.messages: list = []
        self.closed = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def login(self, user, password):
        self.logins.append((user, password))

    def send_message(self, msg):
        self.messages.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def test_send_feedback_email_sends_expected_message(fake_smtp):
    send_feedback_email("버그 신고", "여기서 오류가 발생합니다.", FULL_SECRETS)

    assert len(fake_smtp.instances) == 1
    server = fake_smtp.instances[0]
    assert server.host == "smtp.example.com"
    assert server.port == 465
    assert server.logins == [("sender@example.com", "app-password")]
    assert len(server.messages) == 1

    msg = server.messages[0]
    assert msg["To"] == "operator@example.com"
    assert msg["From"] == "sender@example.com"
    assert msg["Subject"] == "[생기부 도우미] 버그 신고"
    assert "여기서 오류가 발생합니다." in msg.get_content()


def test_send_feedback_email_defaults_port_465(fake_smtp):
    secrets = {k: v for k, v in FULL_SECRETS.items() if k != "SMTP_PORT"}
    send_feedback_email("기타", "내용입니다.", secrets)
    assert fake_smtp.instances[0].port == 465


def test_send_feedback_email_propagates_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)
    with pytest.raises(smtplib.SMTPAuthenticationError):
        send_feedback_email("기타", "내용입니다.", FULL_SECRETS)


# ── feedback_recipient (기본 수신 주소) ──
def test_feedback_recipient_defaults_when_unset():
    """secrets에 FEEDBACK_TO가 없으면 기본 주소로 보낸다."""
    from core.feedback import DEFAULT_FEEDBACK_TO, feedback_recipient

    assert feedback_recipient({}) == DEFAULT_FEEDBACK_TO
    assert feedback_recipient({"FEEDBACK_TO": "   "}) == DEFAULT_FEEDBACK_TO


def test_feedback_recipient_secret_overrides_default():
    from core.feedback import feedback_recipient

    assert feedback_recipient({"FEEDBACK_TO": "other@example.com"}) == "other@example.com"


def test_is_configured_needs_only_smtp_credentials():
    """수신 주소는 기본값이 있으므로 SMTP 자격정보만 있으면 전송 가능하다."""
    from core.feedback import is_configured

    assert is_configured(
        {"SMTP_HOST": "smtp.x", "SMTP_USER": "u@x", "SMTP_PASSWORD": "p"}
    ) is True


def test_send_uses_default_recipient(monkeypatch):
    """FEEDBACK_TO 미설정 시 기본 주소가 To 헤더에 들어간다."""
    import smtplib

    from core.feedback import DEFAULT_FEEDBACK_TO, send_feedback_email

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            sent["login"] = (u, p)

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    send_feedback_email(
        "기능 제안",
        "일괄 처리 속도를 올려주세요",
        {"SMTP_HOST": "smtp.x", "SMTP_USER": "u@x", "SMTP_PASSWORD": "p"},
    )
    assert sent["msg"]["To"] == DEFAULT_FEEDBACK_TO
    assert sent["login"] == ("u@x", "p")
