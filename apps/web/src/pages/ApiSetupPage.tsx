import { Check, ChevronDown, Copy, ExternalLink, KeyRound } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { Badge, Button, Card, Dropdown, MenuItem } from '@/components/ui'
import { copyText } from '@/lib/clipboard'
import { TopBar } from '@/components/layout/TopBar'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { ModelInfo } from '@/types'

function Snippet({ text }: { text: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  return (
    <div className="group relative">
      <pre className="overflow-x-auto rounded-control border border-line bg-elevated px-3 py-2.5 text-sm leading-relaxed">
        <code className="font-mono">{text}</code>
      </pre>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t('복사')}
        title={t('명령을 클립보드로 복사합니다')}
        className="absolute top-1.5 right-1.5 opacity-60 transition-opacity hover:opacity-100 focus:opacity-100"
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

/** Code snippets for calling this instance; address and model come from the live origin and catalogue. */
export function ApiSetupPage() {
  const t = useT()
  const { models } = useStore()
  const base = `${window.location.origin}/llm`

  const chat = models.filter((m) => m.kinds.includes('chat'))
  const [picked, setPicked] = useState<string | null>(null)
  const model = chat.find((m) => m.id === picked) ?? chat[0]
  const modelId = model?.id ?? 'local/qwen3.5-122b-a10b'

  const openaiSnippet = [
    'pip install openai',
    '',
    'from openai import OpenAI',
    '',
    'client = OpenAI(',
    `    base_url="${base}/v1",`,
    `    api_key="<${t('발급받은 키')}>",`,
    ')',
    '',
    'reply = client.chat.completions.create(',
    `    model="${modelId}",`,
    '    messages=[{"role": "user", "content": "안녕하세요"}],',
    ')',
    'print(reply.choices[0].message.content)',
  ].join('\n')

  const streamSnippet = [
    'stream = client.chat.completions.create(',
    `    model="${modelId}",`,
    '    messages=[{"role": "user", "content": "짧은 자기소개 부탁해"}],',
    '    stream=True,',
    ')',
    'for chunk in stream:',
    '    print(chunk.choices[0].delta.content or "", end="", flush=True)',
  ].join('\n')

  const litellmSnippet = [
    'pip install litellm',
    '',
    'from litellm import completion',
    '',
    'reply = completion(',
    `    model="openai/${modelId}",  # openai/ ${t('접두사가 프로토콜을 고른다')}`,
    `    api_base="${base}/v1",`,
    `    api_key="<${t('발급받은 키')}>",`,
    '    messages=[{"role": "user", "content": "안녕하세요"}],',
    ')',
    'print(reply.choices[0].message.content)',
  ].join('\n')

  const curlSnippet = [
    `curl ${base}/v1/chat/completions \\`,
    `  -H "Authorization: Bearer <${t('발급받은 키')}>" \\`,
    '  -H "Content-Type: application/json" \\',
    `  -d '{"model": "${modelId}", "messages": [{"role": "user", "content": "ping"}]}'`,
  ].join('\n')

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('API 연동')}</span>} />
      <PageBody>
        <h1 className="text-2xl font-semibold tracking-tight">{t('API 연동')}</h1>
        <p className="mt-1 text-base text-muted">
          {t('이 인스턴스의 모델을 코드에서 부릅니다. 게이트웨이가 OpenAI 프로토콜을 말하므로, OpenAI SDK 와 LiteLLM SDK 가 그대로 붙습니다.')}
        </p>

        <Card className="mt-6 flex flex-wrap items-center gap-3 p-4">
          <KeyRound size={18} className="text-muted" />
          <div className="min-w-0 flex-1">
            <p className="text-base font-medium">{t('먼저 키를 발급하세요')}</p>
            <p className="text-base text-muted">
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
          <h2 className="text-base font-medium">{t('모델 선택')}</h2>
          <p className="mt-1 mb-2 text-base text-muted">
            {t('고른 모델의 이름이 아래 예제에 그대로 들어갑니다.')}
          </p>
          <Dropdown
            className="min-w-[360px]"
            trigger={() => (
              <button className="flex w-full items-center gap-2 rounded-control border border-line px-3 py-2 text-left text-base transition-colors hover:bg-elevated">
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
                  <span className="shrink-0 text-xs text-faint">{priceLabel(m, t)}</span>
                </span>
              </MenuItem>
            ))}
          </Dropdown>
          <p className="mt-2 font-mono text-sm text-muted">{modelId}</p>
        </section>

        <section className="mt-6">
          <h2 className="text-base font-medium">Python · OpenAI SDK</h2>
          <p className="mt-1 mb-2 text-base text-muted">
            {t('가장 짧은 길입니다. base_url 만 이 인스턴스로 바꾸면 나머지는 여느 OpenAI 코드와 같습니다.')}
          </p>
          <Snippet text={openaiSnippet} />
        </section>

        <section className="mt-5">
          <h2 className="text-base font-medium">{t('스트리밍')}</h2>
          <p className="mt-1 mb-2 text-base text-muted">
            {t('긴 답은 흘려 받습니다. 위에서 만든 client 를 그대로 씁니다.')}
          </p>
          <Snippet text={streamSnippet} />
        </section>

        <section className="mt-5">
          <h2 className="text-base font-medium">Python · LiteLLM SDK</h2>
          <p className="mt-1 mb-2 text-base text-muted">
            {t('이미 LiteLLM 으로 여러 제공자를 오가는 코드라면 이쪽이 자연스럽습니다.')}
          </p>
          <Snippet text={litellmSnippet} />
        </section>

        <section className="mt-5">
          <h2 className="text-base font-medium">{t('바로 확인')}</h2>
          <p className="mt-1 mb-2 text-base text-muted">
            {t('키와 주소가 맞는지 코드를 쓰기 전에 한 줄로 확인합니다.')}
          </p>
          <Snippet text={curlSnippet} />
        </section>

        <section className="mt-6">
          <h2 className="text-base font-medium">{t('알아 둘 것')}</h2>
          <ul className="mt-2 space-y-1.5 text-base text-muted">
            <li>
              {t('이 키로 쓴 양은 사용량 화면의 API 키 항목에 따로 집계됩니다.')}{' '}
              <Link to="/usage" className="underline">
                {t('사용량')}
              </Link>
            </li>
            <li>
              {t('월 한도는 계정에 걸려 있습니다. 키를 여러 개 만들어도 한도가 늘지 않고, 다 쓰면 요청이 거부됩니다.')}
            </li>
            <li>
              {t('임베딩도 같은 주소로 받습니다 — client.embeddings.create(model="local/bge-m3", input=[...]).')}
            </li>
            <li>{t('키를 폐기하면 즉시 막힙니다. 코드 쪽 설정도 함께 지우세요.')}</li>
          </ul>
        </section>
      </PageBody>
    </>
  )
}
