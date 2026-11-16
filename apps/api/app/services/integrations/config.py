"""What the administrator set up for each provider: the one OAuth client this installation
uses, whether it is on, and which features (and so which scopes) people may grant.

Every person connects their own account with that client (a personal gmail.com account as
well as a Workspace one; any Slack workspace where the app is installed). Stored as system
settings; the client secret and Slack's signing secret are sealed
(`settings_store.SECRET_KEYS`) and never returned to the browser."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings as env_settings
from app.services import settings_store

GOOGLE = "google"
SLACK = "slack"
PROVIDERS = (GOOGLE, SLACK)

DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"

#: feature → OAuth scopes a person grants (Google). Drive uses `drive.file` only (files
#: the person picks in Google Picker, or files the app made): a non-sensitive scope, so a
#: personal gmail.com account works without Google's verification or security assessment.
#: (Calendar is on hold, owner decision 2026-11-15: its scopes are sensitive.)
GOOGLE_FEATURES: dict[str, tuple[str, ...]] = {
    "drive": (DRIVE_FILE,),
    "drive_export": (DRIVE_FILE,),
}
#: asked with every Google grant: who the account is, for the settings screen
GOOGLE_IDENTITY = ("openid", "email")

#: feature → Slack user-token scopes (the person's own token)
SLACK_USER_FEATURES: dict[str, tuple[str, ...]] = {
    "read": ("channels:read", "groups:read", "channels:history", "groups:history", "users:read"),
    "post": ("channels:read", "groups:read", "chat:write"),
    "notify": (),
    "bot": (),
}
#: feature → bot-token scopes, granted when the app is installed in a workspace (by
#: whoever connects first there, if the workspace lets members install apps)
SLACK_BOT_FEATURES: dict[str, tuple[str, ...]] = {
    "read": (),
    "post": (),
    "notify": ("chat:write", "im:write"),
    "bot": ("app_mentions:read", "chat:write", "channels:read", "groups:read"),
}

FEATURES: dict[str, tuple[str, ...]] = {
    GOOGLE: tuple(GOOGLE_FEATURES),
    SLACK: tuple(SLACK_USER_FEATURES),
}

#: features on when an administrator enables a provider without choosing
DEFAULT_FEATURES: dict[str, tuple[str, ...]] = {
    GOOGLE: ("drive", "drive_export"),
    SLACK: ("read", "post", "notify", "bot"),
}


def key(provider: str, name: str) -> str:
    return f"integrations.{provider}.{name}"


#: sealed at rest (added to `settings_store.SECRET_KEYS`)
SECRET_NAMES = {
    GOOGLE: ("client_secret",),
    SLACK: ("client_secret", "signing_secret"),
}
SECRET_SETTING_KEYS = frozenset(
    key(provider, name) for provider, names in SECRET_NAMES.items() for name in names
)
assert SECRET_SETTING_KEYS == settings_store.INTEGRATION_SECRET_KEYS


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider: str
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    features: tuple[str, ...] = ()
    #: Google Picker: a browser API key (restricted to this site) and the Cloud project
    #: number; without them Drive files are added by link instead
    picker_key: str = ""
    app_id: str = ""
    #: Slack only: verifies the events Slack sends (the published-agent bot)
    signing_secret: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def usable(self) -> bool:
        """People may connect: on, with the installation's OAuth client."""
        return self.enabled and self.configured

    @property
    def picker(self) -> bool:
        return bool(self.picker_key and self.app_id)

    def allows(self, feature: str) -> bool:
        return self.usable and feature in self.features

    def user_scopes(self) -> list[str]:
        """Scopes a person is asked for, in a stable order without repeats."""
        table = GOOGLE_FEATURES if self.provider == GOOGLE else SLACK_USER_FEATURES
        out: list[str] = list(GOOGLE_IDENTITY) if self.provider == GOOGLE else []
        for feature in FEATURES[self.provider]:
            if feature in self.features:
                out += [s for s in table[feature] if s not in out]
        return out

    def install_scopes(self) -> list[str]:
        """Slack bot scopes a workspace install asks for."""
        out: list[str] = []
        for feature in FEATURES[SLACK]:
            if feature in self.features:
                out += [s for s in SLACK_BOT_FEATURES[feature] if s not in out]
        return out or ["chat:write"]


def _features(raw: str | None, provider: str) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_FEATURES[provider]
    wanted = {part.strip() for part in raw.split(",")}
    return tuple(f for f in FEATURES[provider] if f in wanted)


async def get(provider: str) -> ProviderConfig:
    values = await settings_store.all_values()

    def value(name: str) -> str:
        return (values.get(key(provider, name)) or "").strip()

    return ProviderConfig(
        provider=provider,
        enabled=value("enabled") == "1",
        client_id=value("client_id"),
        client_secret=value("client_secret"),
        features=_features(values.get(key(provider, "features")), provider),
        picker_key=value("picker_key"),
        app_id=value("app_id"),
        signing_secret=value("signing_secret"),
    )


async def base_url() -> str:
    """The address people open the app at; OAuth redirects come back to it."""
    values = await settings_store.all_values()
    stored = (values.get(settings_store.APP_BASE_URL) or "").strip()
    return (stored or env_settings.app_base_url or "").rstrip("/")


async def redirect_uri(provider: str) -> str:
    base = await base_url()
    return f"{base}{env_settings.api_prefix}/integrations/{provider}/callback" if base else ""


async def slack_events_url() -> str:
    base = await base_url()
    return f"{base}{env_settings.api_prefix}/integrations/slack/events" if base else ""


async def offered() -> dict[str, bool]:
    """What the web may offer (public: no client id, no key): Drive picking, Slack."""
    google = await get(GOOGLE)
    slack = await get(SLACK)
    return {"drivePicker": google.allows("drive") and google.picker,
            "slack": slack.usable, "slackBot": slack.allows("bot")}
