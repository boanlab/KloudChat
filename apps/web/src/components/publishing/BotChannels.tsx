import { Bot, Code2, Copy, Eye, KeyRound, Loader2, PanelsTopLeft, Plug, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, ConfirmDialog, Field, Input, LoadingState, Switch, Textarea } from '@/components/ui'
import {
  ApiError,
  errorMessage,
  publicationsApi,
  type BotAdminRead,
  type BotChannel,
  type BotKey,
  type Publication,
  type PublicationStats,
} from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import {
  apiBase,
  CHANNEL_ERROR,
  CHANNEL_HINT,
  CHANNEL_LABEL,
  CHANNELS,
  channelAllowed,
  channelState,
  embedSnippet,
  mcpUrl,
  type ChannelState,
} from '@/lib/publishing'
import { useT } from '@/lib/useT'
import { formatDate, formatDateTime } from '@/lib/utils'

const ICON: Record<BotChannel, typeof Plug> = { embed: PanelsTopLeft, api: Code2, mcp: Plug }

const STATE_LABEL: Record<ChannelState, string> = {
  open: '열림',
  pending: '승인 대기',
  closed: '닫힘',
  unavailable: '이 공개 범위에서는 쓸 수 없음',
}
const STATE_TONE: Record<ChannelState, 'success' | 'accent' | 'neutral' | 'warn'> = {
  open: 'success',
  pending: 'accent',
  closed: 'neutral',
  unavailable: 'neutral',
}

const day = (iso: string | null | undefined) => (iso ? formatDate(iso) : '')

/** Copies a text and says so for a moment. */
function CopyButton({ text, label }: { text: string; label?: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={async () => {
        setCopied(await copyText(text))
        window.setTimeout(() => setCopied(false), 1500)
      }}
    >
      <Copy size={13} />
      {copied ? t('복사됨') : (label ?? t('복사'))}
    </Button>
  )
}

function CodeBlock({ code, testId }: { code: string; testId?: string }) {
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-control border border-line bg-elevated p-3 pr-20 text-xs leading-relaxed" data-testid={testId}>
        <code>{code}</code>
      </pre>
      <div className="absolute top-1.5 right-1.5">
        <CopyButton text={code} />
      </div>
    </div>
  )
}

/**
 * The bot's channels on one card: each one's state, switching it on (asked of an administrator)
 * or off (at once), the sites that may frame it, and the code to paste once it is open.
 */
export function ChannelsCard({ pub, onChange }: { pub: Publication; onChange: (next: Publication) => void }) {
  const t = useT()
  const open = pub.channels ?? {}
  const wantedNow = pub.requestedChannels ?? open
  const [on, setOn] = useState<Record<BotChannel, boolean>>({
    embed: Boolean(wantedNow.embed),
    api: Boolean(wantedNow.api),
    mcp: Boolean(wantedNow.mcp),
  })
  const [origins, setOrigins] = useState((wantedNow.embed?.origins ?? []).join('\n'))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const list = origins.split('\n').map((o) => o.trim()).filter(Boolean)
  const audience = pub.requestedVisibility || pub.visibility
  const opening = CHANNELS.some((c) => on[c] && !open[c]) ||
    (on.embed && list.some((o) => !(open.embed?.origins ?? []).includes(o.toLowerCase().replace(/\/$/, ''))))
  const changed =
    CHANNELS.some((c) => on[c] !== Boolean(wantedNow[c])) ||
    (on.embed && list.join('\n') !== (wantedNow.embed?.origins ?? []).join('\n'))

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      onChange(await publicationsApi.setChannels(pub.id, { embed: { on: on.embed, origins: list }, api: on.api, mcp: on.mcp }))
    } catch (err) {
      const code = err instanceof ApiError ? err.detail : ''
      setError(CHANNEL_ERROR[code] ? t(CHANNEL_ERROR[code]) : errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="space-y-3 p-4" data-testid="publication-channels">
      <div>
        <p className="text-base font-medium">{t('채널')}</p>
        <p className="text-sm text-muted">{t('챗봇 페이지 말고도 다른 사이트, 내 프로그램(API), AI 도구(MCP)에서 이 챗봇을 쓸 수 있습니다. 켜는 것은 관리자가 승인하면 열리고, 끄는 것은 바로 적용됩니다.')}</p>
      </div>
      <ul className="divide-y divide-line rounded-card border border-line">
        {CHANNELS.map((channel) => {
          const Icon = ICON[channel]
          const state = channelState(pub, channel)
          const allowed = channelAllowed(audience, channel)
          return (
            <li key={channel} className="space-y-2 px-3 py-3" data-testid={`channel-${channel}`}>
              <div className="flex flex-wrap items-center gap-2">
                <Icon size={15} aria-hidden className="text-muted" />
                <span className="text-base font-medium">{t(CHANNEL_LABEL[channel])}</span>
                <Badge tone={STATE_TONE[state]}>{t(STATE_LABEL[state])}</Badge>
                <span className="flex-1" />
                <Switch
                  checked={on[channel]}
                  disabled={busy || (!allowed && !on[channel])}
                  label={t(CHANNEL_LABEL[channel])}
                  onChange={(next) => setOn({ ...on, [channel]: next })}
                />
              </div>
              <p className="text-sm text-muted">{t(CHANNEL_HINT[channel])}</p>
              {channel === 'embed' && on.embed && (
                <Field label={t('넣을 사이트 주소')} hint={t('한 줄에 하나, https://로 시작합니다. https://*.example.ac.kr처럼 하위 주소 한 단계를 함께 쓸 수 있습니다. 최대 10개.')}>
                  <Textarea
                    rows={3}
                    value={origins}
                    onChange={(e) => setOrigins(e.target.value)}
                    placeholder="https://www.example.ac.kr"
                    data-testid="channel-origins"
                  />
                </Field>
              )}
              {channel === 'embed' && state === 'open' && (
                <div className="space-y-1">
                  <p className="text-sm text-faint">{t('삽입 코드: 사이트의 </body> 바로 앞에 붙이세요.')}</p>
                  <CodeBlock code={embedSnippet(pub.slug)} testId="embed-snippet" />
                </div>
              )}
            </li>
          )
        })}
      </ul>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {opening && <p className="min-w-0 flex-1 text-sm text-muted">{t('관리자가 승인할 때까지 지금 열린 채널만 씁니다.')}</p>}
        <Button variant="primary" disabled={busy || !changed} onClick={() => void save()} data-testid="channels-save">
          {busy && <Loader2 size={14} className="animate-spin" />}
          {opening ? t('승인 요청') : t('채널 저장')}
        </Button>
      </div>
    </Card>
  )
}

/** When an administrator read this bot's conversations, and the reason they gave. */
export function AdminReadsCard({ pub }: { pub: Publication }) {
  const t = useT()
  const [rows, setRows] = useState<BotAdminRead[] | null>(null)
  useEffect(() => {
    publicationsApi.adminReads(pub.id).then(setRows).catch(() => setRows([]))
  }, [pub.id])
  return (
    <Card className="space-y-2 p-4" data-testid="publication-admin-reads">
      <p className="flex items-center gap-1.5 text-base font-medium">
        <Eye size={14} aria-hidden className="text-muted" />
        {t('관리자 열람 기록')}
      </p>
      {rows === null ? null : rows.length === 0 ? (
        <p className="text-sm text-faint">{t('관리자가 이 챗봇의 대화를 연 적이 없습니다.')}</p>
      ) : (
        <ul className="space-y-1">
          {rows.map((row, i) => (
            <li key={i} className="text-sm">
              {t('관리자가 열람함: {date} · 사유: {reason}')
                .replace('{date}', formatDateTime(row.at))
                .replace('{reason}', row.reason)}
              {row.kind === 'list' && <span className="text-faint"> · {t('대화 목록')}</span>}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-faint">{t('관리자는 남용 신고를 확인할 때만 사유를 남기고 대화를 엽니다. 대화의 개인정보는 가려서 보입니다.')}</p>
    </Card>
  )
}

/** The developer's tab: keys for the API and MCP channels, how to call them, and their use. */
export function ApiTab({ pub }: { pub: Publication }) {
  const t = useT()
  const [keys, setKeys] = useState<BotKey[] | null>(null)
  const [name, setName] = useState('')
  const [made, setMade] = useState<BotKey | null>(null)
  const [revoking, setRevoking] = useState<BotKey | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stats, setStats] = useState<PublicationStats | null>(null)
  useEffect(() => {
    publicationsApi.keys(pub.id).then(setKeys).catch(() => setKeys([]))
    publicationsApi.stats(pub.id, 30).then(setStats).catch(() => undefined)
  }, [pub.id])

  const create = async () => {
    setError(null)
    try {
      const key = await publicationsApi.createKey(pub.id, name.trim())
      setMade(key)
      setName('')
      setKeys([{ ...key, key: undefined }, ...(keys ?? [])])
    } catch (err) {
      const code = err instanceof ApiError ? err.detail : ''
      setError(code === 'too_many_keys' ? t('키는 10개까지 만들 수 있습니다. 쓰지 않는 키를 폐기하세요.') : errorMessage(err, t('키를 만들지 못했습니다.')))
    }
  }

  const key = made?.key ?? 'kcb_YOUR_KEY'
  const base = apiBase()
  // The bot's own first suggested question, as a JSON string (and safe inside the shell's '…').
  const question = JSON.stringify(pub.branding?.suggestedPrompts?.[0]?.trim() || t('무엇을 도와줄 수 있나요?'))
  const shellQuestion = question.replace(/'/g, "'\\''")
  const curl = [
    `curl ${base}/bots/v1/chat/completions \\`,
    `  -H "Authorization: Bearer ${key}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"model": "${pub.slug}", "messages": [{"role": "user", "content": ${shellQuestion}}]}'`,
  ].join('\n')
  const python = [
    'from openai import OpenAI',
    '',
    `client = OpenAI(base_url="${base}/bots/v1", api_key="${key}")`,
    'reply = client.chat.completions.create(',
    `    model="${pub.slug}",`,
    `    messages=[{"role": "user", "content": ${question}}],`,
    ')',
    'print(reply.choices[0].message.content)',
    '# citations: reply.model_extra["kloudchat"]["citations"]',
  ].join('\n')
  const mcpJson = JSON.stringify(
    { mcpServers: { [pub.slug]: { type: 'http', url: mcpUrl(pub.slug), headers: { Authorization: `Bearer ${key}` } } } },
    null,
    2,
  )
  const claudeCode = `claude mcp add --transport http ${pub.slug} ${mcpUrl(pub.slug)} --header "Authorization: Bearer ${key}"`
  const apiOpen = channelState(pub, 'api') === 'open'
  const mcpOpen = channelState(pub, 'mcp') === 'open'

  return (
    <div className="max-w-3xl space-y-4" data-testid="publication-api">
      {!apiOpen && !mcpOpen && (
        <p role="status" className="rounded-control border border-line bg-elevated px-3 py-2 text-base text-muted">
          {t('API·MCP 채널이 아직 열리지 않았습니다. 개요의 채널에서 켜고 관리자 승인을 받으면 아래 키로 부를 수 있습니다. 키는 미리 만들어 둘 수 있습니다.')}
        </p>
      )}
      <Card className="space-y-3 p-4">
        <p className="flex items-center gap-1.5 text-base font-medium">
          <KeyRound size={14} aria-hidden className="text-muted" />
          {t('키')}
        </p>
        <p className="text-sm text-muted">{t('이 챗봇만 부를 수 있는 키입니다. 다른 챗봇이나 KloudChat의 다른 기능에는 쓸 수 없고, 쓴 만큼 내 크레딧이 줄어듭니다.')}</p>
        {made?.key && (
          <div className="space-y-2 rounded-control border border-warn/30 bg-warn/5 p-3" role="status" data-testid="new-key">
            <p className="text-sm text-warn">{t('이 키는 지금 한 번만 보입니다. 안전한 곳에 보관하세요.')}</p>
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <code className="min-w-0 flex-1 break-all text-sm">{made.key}</code>
              <CopyButton text={made.key} />
            </div>
          </div>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-0 flex-1">
            <Field label={t('키 이름')}>
              <Input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder={t('예: 학과 홈페이지 서버')} />
            </Field>
          </div>
          <Button variant="primary" disabled={!name.trim()} onClick={() => void create()} data-testid="key-create">
            {t('키 만들기')}
          </Button>
        </div>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        {keys === null ? (
          <LoadingState />
        ) : keys.length === 0 ? (
          <p className="text-sm text-faint">{t('만든 키가 없습니다.')}</p>
        ) : (
          <ul className="divide-y divide-line" data-testid="key-list">
            {keys.map((k) => (
              <li key={k.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{k.name}</span> <code className="text-faint">{k.preview}</code>
                </span>
                <span className="text-faint">
                  {k.lastUsedAt ? t('마지막 사용 {date}').replace('{date}', day(k.lastUsedAt)) : t('쓴 적 없음')}
                </span>
                <Button size="sm" variant="ghost" onClick={() => setRevoking(k)}>
                  <Trash2 size={13} />
                  {t('폐기')}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="space-y-3 p-4">
        <p className="flex items-center gap-1.5 text-base font-medium">
          <Code2 size={14} aria-hidden className="text-muted" />
          {t('API로 부르기')}
        </p>
        <p className="text-sm text-muted">
          {t('OpenAI 호환 방식입니다. model에는 챗봇 주소를 씁니다. 답을 다 쓴 뒤에 보내므로 stream을 켜도 답이 한 번에 이어서 옵니다. 인용한 자료는 kloudchat.citations에 있습니다.')}
        </p>
        <CodeBlock code={curl} testId="api-curl" />
        <CodeBlock code={python} testId="api-python" />
        <p className="text-sm text-faint">
          {t('이어 묻기: 앞선 대화를 messages에 함께 보내거나, 답의 X-KloudChat-Conversation 머리 값을 다음 요청 머리에 그대로 보냅니다.')}
        </p>
      </Card>

      <Card className="space-y-3 p-4">
        <p className="flex items-center gap-1.5 text-base font-medium">
          <Plug size={14} aria-hidden className="text-muted" />
          {t('MCP로 연결하기')}
        </p>
        <p className="text-sm text-muted">{t('ask 도구 하나를 제공합니다. 같은 키로 연결합니다.')}</p>
        <CodeBlock code={claudeCode} testId="mcp-claude-code" />
        <CodeBlock code={mcpJson} testId="mcp-json" />
      </Card>

      <Card className="space-y-2 p-4">
        <p className="flex items-center gap-1.5 text-base font-medium">
          <Bot size={14} aria-hidden className="text-muted" />
          {t('채널별 질문 (최근 30일)')}
        </p>
        {stats ? (
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="channel-usage">
            {(['web', ...CHANNELS] as const).map((c) => (
              <li key={c} className="rounded-control border border-line px-3 py-2">
                <p className="text-sm text-faint">{c === 'web' ? t('챗봇 페이지') : t(CHANNEL_LABEL[c])}</p>
                <p className="text-lg font-semibold tabular-nums">{(stats.channels?.[c] ?? 0).toLocaleString()}</p>
              </li>
            ))}
          </ul>
        ) : (
          <LoadingState />
        )}
      </Card>

      <ConfirmDialog
        open={revoking !== null}
        onClose={() => setRevoking(null)}
        onConfirm={() => {
          const target = revoking
          setRevoking(null)
          if (!target) return
          void publicationsApi
            .revokeKey(pub.id, target.id)
            .then(() => setKeys((keys ?? []).filter((k) => k.id !== target.id)))
            .catch((err) => setError(errorMessage(err, t('키를 폐기하지 못했습니다.'))))
        }}
        title={t('키 폐기')}
        confirmLabel={t('폐기')}
        description={t('이 키로 부르는 프로그램과 AI 도구는 바로 쓸 수 없게 됩니다. 되돌릴 수 없습니다.')}
      />
    </div>
  )
}
