"""Slack and SMTP notifications for finished reviews."""

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

import httpx

from eagle.credentials import decrypt_json
from eagle.models import ChannelKind, NotificationChannel
from eagle.security import SecretBox

EVENTS = ("review.succeeded", "review.failed")
SLACK_POST_MESSAGE = "https://slack.com/api/chat.postMessage"


class ChannelConfigError(ValueError):
    pass


class NotificationError(Exception):
    pass


def split_channel_config(
    kind: ChannelKind, values: dict[str, Any], existing_secret: dict[str, str] | None = None
) -> tuple[dict[str, str], dict[str, Any]]:
    """Validate a channel config and split it into (secret, public)."""
    existing_secret = existing_secret or {}
    if kind == ChannelKind.SLACK:
        allowed = {"webhook_url", "bot_token", "channel"}
        _reject_unknown(values, allowed)
        submitted = {k: (values.get(k) or "").strip() for k in ("webhook_url", "bot_token")}
        if any(submitted.values()):
            # A new secret replaces the stored one entirely, so switching between
            # webhook and bot token does not leave the old secret behind.
            secret = {k: v for k, v in submitted.items() if v}
        else:
            secret = {k: v for k, v in existing_secret.items() if k in submitted and v}
        public = {"channel": (values.get("channel") or "").strip()}
        if not secret:
            raise ChannelConfigError("Slack needs a webhook_url or a bot_token")
        if "webhook_url" in secret and not secret["webhook_url"].startswith("https://"):
            raise ChannelConfigError("webhook_url must be an https URL")
        if "bot_token" in secret and "webhook_url" not in secret and not public["channel"]:
            raise ChannelConfigError("channel is required when using a bot_token")
        return secret, public

    allowed = {
        "host",
        "port",
        "security",
        "username",
        "password",
        "from_addr",
        "to_addrs",
        "notify_submitter",
    }
    _reject_unknown(values, allowed)
    host = (values.get("host") or "").strip()
    from_addr = (values.get("from_addr") or "").strip()
    if not host or not from_addr:
        raise ChannelConfigError("SMTP needs host and from_addr")
    security = values.get("security") or "starttls"
    if security not in ("starttls", "ssl", "none"):
        raise ChannelConfigError("security must be starttls, ssl or none")
    port = int(values.get("port") or (465 if security == "ssl" else 587))
    to_addrs = values.get("to_addrs") or []
    if isinstance(to_addrs, str):
        to_addrs = [a.strip() for a in to_addrs.split(",") if a.strip()]
    notify_submitter = bool(values.get("notify_submitter", False))
    if not to_addrs and not notify_submitter:
        raise ChannelConfigError("Set to_addrs and/or notify_submitter")
    password = (values.get("password") or "").strip() or existing_secret.get("password", "")
    public = {
        "host": host,
        "port": port,
        "security": security,
        "username": (values.get("username") or "").strip(),
        "from_addr": from_addr,
        "to_addrs": to_addrs,
        "notify_submitter": notify_submitter,
    }
    return ({"password": password} if password else {}), public


def _reject_unknown(values: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(values) - allowed
    if unknown:
        raise ChannelConfigError(f"Unknown fields: {', '.join(sorted(unknown))}")


@dataclass
class ReviewEvent:
    event: str
    review_id: int
    repo: str
    pr_number: int
    pr_url: str
    title: str | None
    agent: str
    model: str | None
    verdict: str | None
    summary: str | None
    error: str | None
    review_url: str
    submitter_email: str | None = None

    @property
    def subject(self) -> str:
        outcome = "finished" if self.event == "review.succeeded" else "failed"
        return f"[Eagle] Review {outcome}: {self.repo}#{self.pr_number}"

    def text(self) -> str:
        lines = [self.subject]
        if self.title:
            lines.append(f"PR: {self.title}")
        lines.append(f"Agent: {self.agent}" + (f" ({self.model})" if self.model else ""))
        if self.verdict:
            lines.append(f"Verdict: {self.verdict}")
        if self.summary:
            lines += ["", self.summary]
        if self.error:
            lines += ["", f"Error: {self.error[:500]}"]
        lines += ["", f"Review: {self.review_url}", f"Pull request: {self.pr_url}"]
        return "\n".join(lines)


class Notifier:
    def __init__(
        self,
        box: SecretBox,
        http_transport: httpx.AsyncBaseTransport | None = None,
        smtp_class: type[smtplib.SMTP] = smtplib.SMTP,
        smtp_ssl_class: type[smtplib.SMTP_SSL] = smtplib.SMTP_SSL,
    ) -> None:
        self._box = box
        self._transport = http_transport
        self._smtp_class = smtp_class
        self._smtp_ssl_class = smtp_ssl_class

    async def send(self, channel: NotificationChannel, event: ReviewEvent) -> None:
        secret = decrypt_json(self._box, channel.encrypted_secret)
        public = channel.public_config or {}
        if channel.kind == ChannelKind.SLACK:
            await self._send_slack(secret, public, event)
        else:
            await self._send_smtp(secret, public, event)

    async def _send_slack(self, secret: dict, public: dict, event: ReviewEvent) -> None:
        text = event.text()
        async with httpx.AsyncClient(transport=self._transport, timeout=15) as client:
            try:
                if secret.get("webhook_url"):
                    resp = await client.post(secret["webhook_url"], json={"text": text})
                    if resp.status_code >= 300:
                        raise NotificationError(f"Slack webhook returned {resp.status_code}")
                    return
                resp = await client.post(
                    SLACK_POST_MESSAGE,
                    headers={"Authorization": f"Bearer {secret['bot_token']}"},
                    json={"channel": public["channel"], "text": text},
                )
            except httpx.HTTPError as exc:
                raise NotificationError(f"Slack request failed: {exc}") from exc
            data = resp.json()
            if not data.get("ok"):
                raise NotificationError(f"Slack API error: {data.get('error', 'unknown')}")

    async def _send_smtp(self, secret: dict, public: dict, event: ReviewEvent) -> None:
        recipients = list(public.get("to_addrs") or [])
        if public.get("notify_submitter") and event.submitter_email:
            recipients.append(event.submitter_email)
        recipients = list(dict.fromkeys(recipients))
        if not recipients:
            return

        message = EmailMessage()
        message["Subject"] = event.subject
        message["From"] = public["from_addr"]
        message["To"] = ", ".join(recipients)
        message.set_content(event.text())

        def _send() -> None:
            security = public.get("security", "starttls")
            cls = self._smtp_ssl_class if security == "ssl" else self._smtp_class
            with cls(public["host"], int(public["port"]), timeout=30) as smtp:
                if security == "starttls":
                    smtp.starttls()
                if public.get("username"):
                    smtp.login(public["username"], secret.get("password", ""))
                smtp.send_message(message)

        try:
            await asyncio.to_thread(_send)
        except (smtplib.SMTPException, OSError) as exc:
            raise NotificationError(f"SMTP send failed: {exc}") from exc
