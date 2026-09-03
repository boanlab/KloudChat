"""Outgoing account mail.

One correspondent — the person who asked for a password reset — and one message.
There is no queue, no template engine and no retry: a reset link is worth almost
nothing ten minutes later, so a failed send is reported to the caller rather than
parked somewhere to be dealt with.

`smtplib` is blocking, so every send goes through a worker thread. The alternative
is another dependency for one message a week.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from app.services import settings_store

log = logging.getLogger(__name__)

#: Long enough for a slow relay, short enough that an administrator pressing
#: "Test" gets an answer rather than a spinner.
_TIMEOUT = 15.0


class MailError(RuntimeError):
    """Delivery failed. The message is written for an administrator to act on."""


def _describe(exc: Exception) -> str:
    """SMTP errors are precise and unreadable; these are the ones that happen."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "인증에 실패했습니다. 사용자 이름과 비밀번호를 확인하세요."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "받는 주소가 거부되었습니다."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "보내는 주소가 거부되었습니다. 릴레이가 소유한 주소인지 확인하세요."
    if isinstance(exc, ssl.SSLError):
        return "TLS 협상에 실패했습니다. 보안 방식(STARTTLS/SSL)과 포트를 확인하세요."
    if isinstance(exc, TimeoutError | OSError):
        return "서버에 연결하지 못했습니다. 주소와 포트를 확인하세요."
    return "메일을 보내지 못했습니다. 설정을 확인하세요."


def _send_blocking(config: dict[str, str], message: EmailMessage) -> None:
    host = config["host"]
    security = config["security"] or "starttls"
    port = int(config["port"] or (465 if security == "ssl" else 587))

    if security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            host, port, timeout=_TIMEOUT, context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(host, port, timeout=_TIMEOUT)
    try:
        if security == "starttls":
            server.starttls(context=ssl.create_default_context())
        if config["username"]:
            server.login(config["username"], config["password"])
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 — the mail is already sent or already lost
            server.close()


async def send(*, to: str, subject: str, body: str) -> None:
    """Delivers one plain-text message, or raises `MailError`.

    Plain text on purpose. A reset mail is a sentence and a link; sending it as
    HTML would buy a font and cost the reader a phishing-shaped email.
    """
    config = await settings_store.smtp_config()
    if not config["host"] or not config["sender"]:
        raise MailError("메일 서버가 설정되지 않았습니다.")

    name, address = parseaddr(config["sender"])
    if not address:
        raise MailError("보내는 주소가 올바르지 않습니다.")

    message = EmailMessage()
    message["From"] = formataddr((name or "KloudChat", address))
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        await asyncio.to_thread(_send_blocking, config, message)
    except Exception as exc:  # noqa: BLE001 — every failure is reported the same way
        log.warning("mail send failed to %s: %s", to.replace("\n", " ")[:200], exc)
        raise MailError(_describe(exc)) from exc


def reset_message(*, name: str, link: str, minutes: int) -> tuple[str, str]:
    """Subject and body for a password reset.

    Says who asked and what to do if it was not them. A reset mail that arrives
    unbidden is the first sign someone is trying an account, and the reader can
    only act on that if the mail says so.
    """
    subject = "KloudChat 비밀번호 재설정"
    body = (
        f"{name}님,\n\n"
        "KloudChat 비밀번호 재설정이 요청되었습니다. 아래 주소에서 새 비밀번호를 정하세요.\n\n"
        f"{link}\n\n"
        f"이 링크는 {minutes}분 뒤에 만료되고, 한 번만 쓸 수 있습니다.\n"
        "직접 요청하지 않았다면 이 메일은 무시해도 됩니다. 비밀번호는 그대로입니다.\n"
    )
    return subject, body


def verification_message(*, name: str, link: str, minutes: int) -> tuple[str, str]:
    """Subject and body for confirming a signup address."""
    subject = "KloudChat 가입 확인"
    body = (
        f"{name}님,\n\n"
        "KloudChat 에 이 주소로 가입 요청이 들어왔습니다. 본인이 맞다면 아래 주소를 눌러 "
        "확인해 주세요.\n\n"
        f"{link}\n\n"
        f"이 링크는 {minutes}분 뒤에 만료되고, 한 번만 쓸 수 있습니다.\n"
        "가입한 적이 없다면 이 메일은 무시해도 됩니다. 확인하지 않은 요청은 그대로 지나갑니다.\n"
    )
    return subject, body


__all__ = ["MailError", "reset_message", "send", "verification_message"]
