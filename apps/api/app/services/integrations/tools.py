"""Which integration tools a turn gets, and whether the turn is about the person's own Drive or
Slack at all.

A tool is offered only when the person connected the provider, granted the scope and the
administrator enabled the feature. A turn gets them when its words are about that service
(`wanted`) or its agent lists them; such a turn handles internal material, so under a strict-local
policy only those models read it (`routers/sessions`). Published agents never get them: a visitor
must not reach the publisher's accounts."""

from __future__ import annotations

import re

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User
from app.services.integrations import accounts, config, drive, slack
from app.services.tools.base import Tool

#: a request about the person's Google Drive files
_DRIVE = re.compile(
    r"드라이브|구글\s?(?:문서|독스|docs|시트|스프레드시트|슬라이드)|"
    r"google\s?(?:drive|docs|sheets|slides)|docs\.google\.com|drive\.google\.com",
    re.I,
)
#: a request about the person's Slack
_SLACK = re.compile(r"슬랙|slack", re.I)
#: tool names by provider
NAMES: dict[str, tuple[str, ...]] = {
    config.GOOGLE: (drive.SEARCH, drive.READ, drive.IMPORT, drive.EXPORT),
    config.SLACK: (slack.LIST, slack.READ, slack.POST),
}
ALL_NAMES = frozenset(name for names in NAMES.values() for name in names)
#: provider → its cue
_CUES: dict[str, tuple[re.Pattern[str], ...]] = {
    config.GOOGLE: (_DRIVE,),
    config.SLACK: (_SLACK,),
}

#: picker blurbs; the model reads `Tool.description`
BLURBS: dict[str, str] = {
    drive.SEARCH: "내가 고른 드라이브 파일에서 찾습니다.",
    drive.READ: "드라이브 파일의 글을 읽습니다.",
    drive.IMPORT: "드라이브 파일을 프로젝트 자료로 넣습니다.",
    drive.EXPORT: "확인을 받은 뒤 문서를 내 드라이브에 저장합니다.",
    slack.LIST: "내가 속한 Slack 채널을 봅니다.",
    slack.READ: "Slack 채널의 최근 대화를 읽습니다.",
    slack.POST: "확인을 받은 뒤 Slack에 메시지를 보냅니다.",
}


def wanted(text: str, provider: str) -> bool:
    """Whether the words are about this provider's service."""
    return any(cue.search(text or "") for cue in _CUES.get(provider, ()))


async def granted_features(db: AsyncSession, user: User) -> dict[str, set[str]]:
    """Provider → features the person granted and the administrator allows."""
    out: dict[str, set[str]] = {}
    for provider, row in (await accounts.connected(db, user.id)).items():
        cfg = await config.get(provider)
        features = {f for f in cfg.features if accounts.granted(row, f)}
        if features:
            out[provider] = features
    return out


def provider_tools(provider: str, features: set[str]) -> list[Tool]:
    if provider == config.GOOGLE:
        return drive.tools(features)
    return slack.tools(features)


async def turn_tools(db: AsyncSession, user: User, text: str,
                     agent_tools: list[str] | None = None) -> list[Tool]:
    """The integration tools this turn gets (empty when it is not about them)."""
    features = await granted_features(db, user)
    if not features:
        return []
    listed = set(agent_tools or [])
    out: list[Tool] = []
    for provider, granted in features.items():
        offered = provider_tools(provider, granted)
        if offered and (wanted(text, provider) or listed & {t.name for t in offered}):
            out += offered
    return sorted(out, key=lambda tool: tool.name)


async def catalog_rows(db: AsyncSession, user: User) -> list[dict[str, object]]:
    """Agent tool picker rows for the person's connected services."""
    groups = {config.GOOGLE: "Google 드라이브", config.SLACK: "Slack"}
    rows: list[dict[str, object]] = []
    for provider, granted in (await granted_features(db, user)).items():
        for tool in provider_tools(provider, granted):
            rows.append({"name": tool.name, "label": tool.title or tool.label,
                         "available": True, "group": groups[provider],
                         "title": tool.title or tool.label,
                         "description": BLURBS.get(tool.name, "")})
    return rows
