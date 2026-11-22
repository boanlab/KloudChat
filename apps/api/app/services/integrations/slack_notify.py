"""A long job's notice in Slack for people who enabled it (설정 › 연결 › Slack).

Same content as the web push (`push.Notice`: title, link; never the written text; title omitted
for internal material). Sent as a DM from the app's bot, else as the person to their chosen
channel."""

from __future__ import annotations

import logging

from app.core.db import SessionLocal
from app.services.integrations import accounts, config
from app.services.integrations.http import ProviderError
from app.services.integrations.slack import Slack
from app.services.integrations.slack_bot import bot_call

log = logging.getLogger(__name__)


def target(prefs: dict | None) -> dict | None:
    """The person's choice: `{"channel": "" (my DMs) | id, "name"}`, or None when off."""
    found = (prefs or {}).get("notify")
    return found if isinstance(found, dict) and found.get("on") else None


async def deliver(user_id: str, title: str, label: str = "", url: str = "/",
                  internal: bool = False) -> bool:
    """Sends one notice; never raises. True when Slack took it."""
    try:
        cfg = await config.get(config.SLACK)
        if not cfg.allows("notify"):
            return False
        async with SessionLocal() as db:
            row = await accounts.account(db, user_id, config.SLACK)
            chosen = target(row.prefs if row else None)
            if row is None or row.status != "connected" or chosen is None:
                return False
            base = await config.base_url()
            link = f"{base}{url}" if base and url.startswith("/") else ""
            text = title + (f" · {label[:80]}" if label and not internal else "")
            if link:
                text += f" <{link}|열기>"
            channel = str(chosen.get("channel") or "")
            bot = await accounts.bot_token(db, row.team_id)
            if not channel and bot:
                opened = await bot_call(bot, "conversations.open", users=row.external_id)
                await bot_call(bot, "chat.postMessage",
                               channel=(opened.get("channel") or {}).get("id"), text=text)
                return True
            if not accounts.granted(row, "post"):
                return False
            await Slack(db, row).post(channel or row.external_id, text)
            return True
    except ProviderError as exc:
        log.info("slack notice not sent: %s", exc.code)
    except Exception as exc:  # noqa: BLE001 — a notice must never disturb the job
        log.warning("slack notice failed (%s)", type(exc).__name__)
    return False
