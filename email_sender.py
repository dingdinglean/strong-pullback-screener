from __future__ import annotations

import os
import smtplib
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable


DEFAULT_SUBJECT = "Strong Pullback Screener Report"


class EmailConfigError(RuntimeError):
    """Raised when SEND_EMAIL is enabled but SMTP settings are incomplete."""


@dataclass(frozen=True)
class EmailConfig:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    email_to: tuple[str, ...]
    subject_base: str


def _split_recipients(value: str) -> tuple[str, ...]:
    raw_parts = value.replace(";", ",").split(",")
    return tuple(part.strip() for part in raw_parts if part.strip())


def load_email_config() -> EmailConfig:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_raw = os.getenv("SMTP_PORT", "587")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to_raw = os.getenv("EMAIL_TO")
    subject_base = os.getenv("EMAIL_SUBJECT", DEFAULT_SUBJECT).strip() or DEFAULT_SUBJECT

    missing = [
        name
        for name, value in {
            "SMTP_HOST": smtp_host,
            "SMTP_USER": smtp_user,
            "SMTP_PASSWORD": smtp_password,
            "EMAIL_TO": email_to_raw,
        }.items()
        if not value
    ]
    if missing:
        raise EmailConfigError(
            "missing required GitHub Secrets or environment variables: "
            + ", ".join(missing)
        )

    try:
        smtp_port = int(smtp_port_raw)
    except ValueError as exc:
        raise EmailConfigError("SMTP_PORT must be an integer") from exc

    recipients = _split_recipients(email_to_raw or "")
    if not recipients:
        raise EmailConfigError("EMAIL_TO must contain at least one recipient")

    return EmailConfig(
        smtp_host=smtp_host or "",
        smtp_port=smtp_port,
        smtp_user=smtp_user or "",
        smtp_password=smtp_password or "",
        email_to=recipients,
        subject_base=subject_base,
    )


def build_subject(subject_base: str, a_candidate_count: int) -> str:
    suffix = "A Candidates Found" if a_candidate_count > 0 else "No A Candidates Today"
    return f"{subject_base} - {suffix}"


def send_report_email(
    report_body: str,
    report_paths: Iterable[Path],
    a_candidate_count: int,
    subject_override: str | None = None,
    retries: int = 0,
) -> None:
    config = load_email_config()
    subject = subject_override or build_subject(config.subject_base, a_candidate_count)
    # Recipients are private configuration.  Keep Actions logs useful without
    # echoing an address that might not be masked by GitHub.
    print("Sending email to configured recipient(s)")

    message = EmailMessage()
    message["From"] = config.smtp_user
    message["To"] = ", ".join(config.email_to)
    message["Subject"] = subject
    message.set_content(report_body)

    for path in report_paths:
        if not path.exists():
            continue
        if path.suffix.lower() == ".csv":
            subtype = "csv"
        elif path.suffix.lower() == ".md":
            subtype = "markdown"
        else:
            subtype = "plain"
        message.add_attachment(
            path.read_bytes(),
            maintype="text",
            subtype=subtype,
            filename=path.name,
        )

    last_error: Exception | None = None
    def deliver_with_starttls(port: int) -> None:
        with smtplib.SMTP(config.smtp_host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(config.smtp_user, config.smtp_password)
            smtp.send_message(message)

    def deliver_ssl() -> None:
        with smtplib.SMTP_SSL(config.smtp_host, 465, timeout=30) as smtp:
            smtp.login(config.smtp_user, config.smtp_password)
            smtp.send_message(message)

    def deliver_once() -> None:
        # Use the secret-configured transport first.  If the SMTP server resets
        # a connection before authentication, try the provider's other standard
        # submission transport.  No credentials are ever sent without TLS.
        transports = [("ssl", 465)] if config.smtp_port == 465 else [("starttls", config.smtp_port)]
        if ("ssl", 465) not in transports:
            transports.append(("ssl", 465))
        if ("starttls", 587) not in transports:
            transports.append(("starttls", 587))
        transport_error: Exception | None = None
        for mode, port in transports:
            try:
                if mode == "ssl":
                    deliver_ssl()
                else:
                    deliver_with_starttls(port)
                return
            except (OSError, smtplib.SMTPServerDisconnected) as exc:
                transport_error = exc
                print("::warning::SMTP transport disconnected; trying secure submission fallback", file=sys.stderr)
        raise transport_error or RuntimeError("No SMTP transport available")

    for attempt in range(retries + 1):
        try:
            deliver_once()
            print("Email sent successfully")
            return
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                print(f"::warning::SMTP attempt {attempt + 1} failed: {exc!r}; retrying once", file=sys.stderr)
                time.sleep(2)
            else:
                print(f"::warning::Email send failed after {attempt + 1} attempts: {exc!r}", file=sys.stderr)
    raise last_error or RuntimeError("Email send failed")


def send_test_failure_alert() -> None:
    """Tell recipients the daily stock report was withheld because tests failed.

    Uses the same SMTP settings as the daily report and a different subject
    and body, so a test failure cannot be mistaken for a ranked scan.
    """
    send_report_email(
        "【美股强势雷达】单元测试失败，今日股票日报未发送。\n请查看 GitHub Actions 日志。\n",
        [],
        0,
        subject_override="【美股强势雷达】单元测试失败｜日报未发送",
        retries=1,
    )
