import { Check, ChevronDown, Copy, ExternalLink, KeyRound } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { Badge, Button, Card, Dropdown, MenuItem } from '@/components/ui'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { ModelInfo } from '@/types'

/**
 * How to attach a coding agent to this instance.
 *
 * The address is built from the origin currently being viewed rather than
 * written into the copy: people reach the instance on different addresses, and
 * a hard-coded one is right for exactly one of them.
 *
 * The model is picked from the catalogue for the same reason. A list written
 * into prose starts handing out wrong names the moment the line-up changes.
 */

function Snippet({ text }: { text: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  return (
    <div className="group relative">
      <pre className="overflow-x-auto rounded-lg border border-line bg-elevated px-3 py-2.5 text-[12px] leading-relaxed">
        <code className="font-mono">{text}</code>
      </pre>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t('복사')}
        className="absolute top-1.5 right-1.5 opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
        onClick={async () => {
          if (!(await copyText(text))) return
          setCopied(true)
          setTimeout(() => setCopied(false), 1400)
        }}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </Button>
    </div>
  )
}

function priceLabel(m: ModelInfo, t: (s: string) => string): string {
  if (m.creditCost === 0 && m.inputCreditCost === 0) return t('무료')
  return t('1k당 {in} / {out}')
    .replace('{in}', m.inputCreditCost.toLocaleString())
    .replace('{out}', m.creditCost.toLocaleString())
}

export function AgentSetupPage() {
  const t = useT()
  const { models } = useStore()
  const base = `${window.location.origin}/llm`

  // Every model that handles conversation. Image- and audio-only models are
  // no use to a coding agent.
  const chat = models.filter((m) => m.kinds.includes('chat'))
  const [picked, setPicked] = useState<string | null>(null)
  const model = chat.find((m) => m.id === picked) ?? chat[0]
  const modelId = model?.id ?? 'local/qwen3.6-35b'

  return (
    <PageBody>
      <h1 className="text-2xl font-semibold tracking-tight">{t('AI 에이전트 연동')}</h1>
      <p className="mt-1 text-[13px] text-muted">
        {t('Claude Code, Codex 같은 코딩 에이전트를 이 인스턴스에 연결합니다. 발급받은 키로 인증하며, 사용량과 한도는 그 키를 따라갑니다.')}
      </p>

      <Card className="mt-6 flex flex-wrap items-center gap-3 p-4">
        <KeyRound size={18} className="text-muted" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{t('먼저 키를 발급하세요')}</p>
          <p className="text-[13px] text-muted">
            {t('발급 직후 한 번만 보이고 다시 확인할 수 없습니다.')}
          </p>
        </div>
        <Link to="/settings/keys">
          <Button>
            {t('API 키 발급')} <ExternalLink size={14} />
          </Button>
        </Link>
      </Card>

      <section className="mt-6">
        <h2 className="text-sm font-medium">{t('모델 선택')}</h2>
        <p className="mt-1 mb-2 text-[13px] text-muted">
          {t('고른 모델의 이름이 아래 설정에 그대로 들어갑니다.')}
        </p>
        <Dropdown
          className="min-w-[360px]"
          trigger={() => (
            <button className="flex w-full items-center gap-2 rounded-lg border border-line px-3 py-2 text-left text-[13px] transition-colors hover:bg-elevated">
              <span className="min-w-0 flex-1 truncate">
                {model ? model.label : t('사용 가능한 모델 없음')}
              </span>
              {model && <Badge>{priceLabel(model, t)}</Badge>}
              <ChevronDown size={14} className="shrink-0 text-faint" />
            </button>
          )}
        >
          {chat.map((m) => (
            <MenuItem key={m.id} onClick={() => setPicked(m.id)}>
              <span className="flex min-w-0 flex-1 items-center gap-2">
                <span className="min-w-0 flex-1 truncate">{m.label}</span>
                <span className="shrink-0 text-[11px] text-faint">{priceLabel(m, t)}</span>
              </span>
            </MenuItem>
          ))}
        </Dropdown>
        <p className="mt-2 font-mono text-[12px] text-muted">{modelId}</p>
      </section>

      <section className="mt-6">
        <h2 className="text-sm font-medium">Claude Code</h2>
        <p className="mt-1 mb-2 text-[13px] text-muted">{t('Anthropic 형식으로 주고받습니다.')}</p>
        <Snippet
          text={`export ANTHROPIC_BASE_URL=${base}\nexport ANTHROPIC_AUTH_TOKEN=<${t('발급받은 키')}>\nexport ANTHROPIC_MODEL=${modelId}`}
        />
      </section>

      <section className="mt-5">
        <h2 className="text-sm font-medium">{t('Codex · OpenAI 호환 도구')}</h2>
        <p className="mt-1 mb-2 text-[13px] text-muted">
          {t('OpenAI 형식으로 주고받습니다. 주소 끝에 /v1 이 붙는 것에 주의하세요.')}
        </p>
        <Snippet
          text={`export OPENAI_BASE_URL=${base}/v1\nexport OPENAI_API_KEY=<${t('발급받은 키')}>\nexport OPENAI_MODEL=${modelId}`}
        />
      </section>

      <section className="mt-6">
        <h2 className="text-sm font-medium">{t('알아 둘 것')}</h2>
        <ul className="mt-2 space-y-1.5 text-[13px] text-muted">
          <li>
            {t('이 키로 쓴 양은 사용량 화면의 API 키 항목에 따로 집계됩니다.')}{' '}
            <Link to="/usage" className="underline">
              {t('사용량')}
            </Link>
          </li>
          <li>
            {t('월 한도는 계정에 걸려 있습니다. 키를 여러 개 만들어도 한도가 늘지 않고, 다 쓰면 요청이 거부됩니다.')}
          </li>
          <li>{t('키를 폐기하면 즉시 막힙니다. 도구 쪽 설정도 함께 지우세요.')}</li>
          <li>
            {t('이 주소는 브라우저에서 접속한 것과 같은 곳입니다. 사내망 밖에서 쓰려면 그 망에 연결한 뒤 사용하세요.')}
          </li>
        </ul>
      </section>
    </PageBody>
  )
}
