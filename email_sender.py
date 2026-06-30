from __future__ import annotations

import os
import smtplib
import sys
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
) -> None:
    config = load_email_config()
    subject = subject_override or build_subject(config.subject_base, a_candidate_count)
    print(f"Sending email to: {', '.join(config.email_to)}")

    message = EmailMessage()
    message["From"] = config.smtp_user
    message["To"] = ", ".join(config.email_to)
    message["Subject"] = subject
    message.set_content(report_body)

    for path in report_paths:
        if not path.exists():
            continue
        subtype = "csv" if path.suffix.lower() == ".csv" else "markdown"
        message.add_attachment(
            path.read_bytes(),
            maintype="text",
            subtype=subtype,
            filename=path.name,
        )

    try:
        if config.smtp_port == 465:
            with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=30) as smtp:
                smtp.login(config.smtp_user, config.smtp_password)
                smtp.send_message(message)
            print("Email sent successfully")
            return

        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(config.smtp_user, config.smtp_password)
            smtp.send_message(message)
        print("Email sent successfully")
    except Exception as exc:
        print(f"Email send failed: {exc!r}", file=sys.stderr)
        raise
