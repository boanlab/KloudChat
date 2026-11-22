# Google Drive and Slack integrations

Each person connects **their own** accounts under **Settings › Connections**: a personal
Google account (gmail.com) works as well as a Google Workspace one. Nobody connects an
organisation-wide account, and nobody else can use a person's connection. A published agent
never gets these tools: a visitor must not reach the publisher's own files or messages.

One installation needs **one OAuth client per provider**, created and configured by the
KloudChat administrator. That is how OAuth works: the client identifies this installation to
Google or Slack, and every person grants that client access to their own account.

Google Calendar and Gmail are not offered: their scopes are sensitive or restricted and
require Google's verification.

## What people get

| Service | Tools the agent may use | Writes |
| --- | --- | --- |
| Google Drive | `drive_search` (files the app may open), `drive_read_file` (Docs exported as .docx, Sheets as .xlsx, Slides as .pptx, other files downloaded), `drive_import_to_project` | `drive_export`: a report, deck or office document saved to the person's Drive |
| Slack | `slack_list_channels`, `slack_read_channel` (only channels the person is in, up to 31 days back) | `slack_post_message`: a message from the person; the card lets them pick another of their channels or edit the text |

Outside the agent:

- **Composer › + › 드라이브에서 가져오기** opens Google Picker; picked files are attached.
- **Project › 지식 › 드라이브** brings picked files in as project material, with their Drive
  link. The refresh button on such a file syncs it again when Drive says it changed.
- **Settings › Connections › Slack** can send a long job's notice to the person's own
  direct messages (by the app's bot where the app is installed, else as themselves) or to a
  channel they are in. The notice carries the job's title and a link, never what was
  written; for internal material the title is left out.
- **A published agent in Slack**: on the agent's publication page the publisher adds channels
  they are in; an @mention of the app there runs a published-bot turn and the answer goes in
  the thread. It follows the publication rules: the publisher's account and credits, the
  version's strict-local model, the bot's limits. A channel inside the organisation is the
  internal audience; a channel shared with another organisation (Slack Connect) is an outside
  one and is answered only once an administrator approved the bot for outside audiences. The
  first answer to each person says that their words are processed and recorded under the
  publisher's account.

Every write waits for the person: the tool stores what it would send and the chat shows a
confirmation card; nothing reaches the provider until **확인** is pressed. Cards expire after
24 hours. Each confirmed write, each failed one, each import and each connect and disconnect
is in the audit log (`integration.*`).

### Privacy and the data boundary

Drive files and Slack messages are internal material:

- A turn gets the tools only when its words are about the service (or its agent lists them),
  and such a turn counts as handling internal material. With **internal material on
  strict-local models** (Governance, on by default) it is answered by a strict-local model,
  and an outside model never receives the tools.
- Tool results go through the same personal-data detection as every other tool result:
  masked before an outside model reads them, kept as is for a strict-local one.
- External web search keeps its own consent rules.

### Limits

- Downloads and exports stop at the upload limit (`KCHAT_MAX_UPLOAD_MB`). Google itself exports at
  most 10 MB of a Docs/Sheets/Slides file.
- Rate limits and server errors are retried with backoff (`Retry-After` honoured); lists
  follow page tokens up to a cap (50 files per Drive search).

### Tokens

Access and refresh tokens are sealed with the settings key (`SECRET_KEY`, else the
`JWT_SECRET`-derived key), refreshed before they expire and never sent to the browser,
with one exception: **Google Picker** runs in the person's browser and needs their own
short-lived access token. That token carries only `drive.file` and the person's address.
Disconnecting revokes the grant at the provider and deletes the tokens; so does deleting
the account, and so does an administrator turning a provider off (for everyone).

## Google Drive setup (administrator)

Drive uses the **`drive.file`** scope only: the app can open the files a person picks in
Google Picker, and the files it saves for them — never the rest of their Drive. `drive.file`
is a non-sensitive scope, so a personal account needs no Google verification or security
assessment, and the app is not limited to 100 test users.

1. **Project.** In the [Google Cloud console](https://console.cloud.google.com/), create a
   project (or choose one) and note its **project number** (Dashboard → Project info).
2. **APIs.** Enable the **Google Drive API** and the **Google Picker API**
   (*APIs & Services → Library*).
3. **Consent screen.** *Google Auth Platform → Branding / Audience*:
   - Audience **External** lets any Google account connect, gmail.com included. Choose
     **Internal** only if everyone uses your Google Workspace domain.
   - Publish the app (*In production*). With only non-sensitive scopes there is no scope
     verification or security assessment; Google may still verify the brand (app name,
     logo, home page and privacy policy on a domain you own). An app left in *Testing*
     works only for the test users you list, and their grants expire after 7 days.
   - *Data access*: add `openid`, `.../auth/userinfo.email` and `.../auth/drive.file`.
4. **OAuth client.** *Clients → Create client → Web application*:
   - Authorized redirect URI: `https://<your address>/api/integrations/google/callback`
     (System › 연동 shows the exact value; it is built from the service address on the
     Mail tab).
   - Authorized JavaScript origin: `https://<your address>` (for Google Picker).
   - Copy the **client ID** and **client secret**.
5. **Picker key.** *Credentials → Create credentials → API key*. Restrict it to *Websites*
   `https://<your address>/*` and to the **Google Picker API**.
6. In KloudChat, **System › 연동 › Google 드라이브**: paste the client ID, client secret,
   Picker API key and project number, choose the features, and switch it on.

The app's content-security policy allows the Picker: its loader script comes from
`apis.google.com` (scripts from `https:` are already allowed) and its dialog is a frame from
`https://docs.google.com` (`frame-src`, `apps/web/security-headers.conf`).

Without a Picker key the Drive features stay off: with `drive.file` a file the person has not
picked cannot be opened, so there is no list-based fallback.

## Slack setup (administrator)

Each person authorizes the app with their own Slack account (a user token). Where the app is
not yet installed in their workspace, connecting installs it, if that workspace lets members
install apps; otherwise Slack refuses and the person is told to ask their Slack admin. The
bot (notices, the published agent) uses the install's bot token.

1. At [api.slack.com/apps](https://api.slack.com/apps) create an app (*From scratch*). To let
   people of several workspaces connect, turn on **Manage Distribution** (public
   distribution; no Slack Marketplace listing is needed).
2. **OAuth & Permissions**:
   - Redirect URL: `https://<your address>/api/integrations/slack/callback`
     (System › 연동 shows it).
   - **User Token Scopes**: `channels:read`, `groups:read`, `channels:history`,
     `groups:history`, `users:read`, `chat:write`.
   - **Bot Token Scopes**: `chat:write`, `im:write`, `app_mentions:read`, `channels:read`,
     `groups:read`.
   - Token rotation may be on or off; refresh tokens are used when Slack issues them.
3. **Event Subscriptions** (only for published agents in Slack): enable, Request URL
   `https://<your address>/api/integrations/slack/events`, bot event `app_mention`. Slack
   must reach this address from the internet.
4. **Basic Information**: copy the **Client ID**, **Client Secret** and **Signing Secret**.
5. In KloudChat, **System › 연동 › Slack**: paste them, choose the features, switch it on.

System › 연동 lists the workspaces where the app is installed. Mark your organisation's own
workspace(s) **우리 조직 워크스페이스**: only there do published agents answer as for members.
Any other workspace, a channel shared with another organisation, and a person from another
organisation count as an outside audience (a bot approved for outside audiences only).
**봇 제거** revokes the bot token there.

## Settings reference

| Setting key | Meaning |
| --- | --- |
| `integrations.<provider>.enabled` | `1` on, `0` off (turning it off disconnects everyone) |
| `integrations.<provider>.client_id` | OAuth client ID |
| `integrations.<provider>.client_secret` | OAuth client secret (sealed, never returned) |
| `integrations.<provider>.features` | Comma list of the features people may grant |
| `integrations.google.picker_key` | Google Picker browser API key |
| `integrations.google.app_id` | Google Cloud project number |
| `integrations.slack.signing_secret` | Slack signing secret (sealed, never returned) |

All are edited on **System › 연동**; the redirect URIs are built from the service address
(`app.base_url`, Mail tab, or `KCHAT_APP_BASE_URL`).

## Tests

The unit and API tests mock both providers with `httpx.MockTransport`
(`apps/api/tests/integration_fixtures.py`), and the e2e spec `integrations.spec.ts` answers
every request with fixtures and stubs Google Picker in the page. No test calls Google or
Slack.
