import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { Field, Input, Select, Switch, Textarea } from '@/components/ui'
import type { BotLimits, ShowProcess } from '@/lib/api'
import { RETENTION_DAYS, SHOW_PROCESS_LABEL, type SettingsDraft } from '@/lib/publishing'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

const LIMIT_FIELDS: { key: Exclude<keyof BotLimits, 'creditsPerDay'>; label: string }[] = [
  { key: 'perVisitorPerMinute', label: '한 사람이 1분에 보낼 수 있는 질문' },
  { key: 'perVisitorPerDay', label: '한 사람이 하루에 보낼 수 있는 질문' },
  { key: 'perIpPerMinute', label: '로그인하지 않은 방문자: 같은 주소에서 1분에 보내는 질문' },
  { key: 'perIpPerDay', label: '로그인하지 않은 방문자: 같은 주소에서 하루에 보내는 질문' },
  { key: 'perBotPerMinute', label: '이 챗봇이 1분에 받는 질문' },
  { key: 'turnsPerDay', label: '하루에 받는 질문' },
  { key: 'maxInputChars', label: '질문 하나의 글자 수' },
  { key: 'turnsPerConversation', label: '대화 하나의 질문 수' },
  { key: 'concurrency', label: '동시에 답하는 질문' },
]

export function PublicationSettingsForm({
  draft,
  defaults,
  onChange,
  viewChanged,
}: {
  draft: SettingsDraft
  defaults: BotLimits
  onChange: (next: SettingsDraft) => void
  /** Enabling 「대화 열람」 or changing retention re-asks visitors to read the notice. */
  viewChanged?: boolean
}) {
  const t = useT()
  const [limitsOpen, setLimitsOpen] = useState(false)
  const set = (patch: Partial<SettingsDraft>) => onChange({ ...draft, ...patch })
  return (
    <div className="space-y-4">
      <Field label={t('이름')}>
        <Input value={draft.title} maxLength={80} onChange={(e) => set({ title: e.target.value })} />
      </Field>
      <Field label={t('설명')} hint={t('챗봇 목록과 챗봇 화면 위쪽에 보입니다.')}>
        <Textarea rows={2} value={draft.description} maxLength={400} onChange={(e) => set({ description: e.target.value })} />
      </Field>
      <Field label={t('첫 인사')} hint={t('첫 질문 전에 보이는 안내입니다.')}>
        <Textarea rows={2} value={draft.greeting} maxLength={400} onChange={(e) => set({ greeting: e.target.value })} />
      </Field>
      <Field label={t('추천 질문')} hint={t('한 줄에 하나, 네 개까지. 누르면 그대로 보냅니다.')}>
        <Textarea rows={3} value={draft.prompts} onChange={(e) => set({ prompts: e.target.value.split('\n').slice(0, 4).join('\n') })} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t('답을 만드는 과정 보여 주기')}>
          <Select value={draft.showProcess} onChange={(e) => set({ showProcess: e.target.value as ShowProcess })}>
            {(['none', 'summary', 'full'] as const).map((id) => (
              <option key={id} value={id}>
                {t(SHOW_PROCESS_LABEL[id])}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t('대화 보관 기간')} hint={t('마지막 질문 뒤 이 기간이 지나면 대화를 지웁니다.')}>
          <Select value={String(draft.retentionDays)} onChange={(e) => set({ retentionDays: Number(e.target.value) })}>
            {RETENTION_DAYS.map((d) => (
              <option key={d} value={d}>
                {t('{n}일').replace('{n}', String(d))}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="flex items-start justify-between gap-3 rounded-card border border-line px-3.5 py-3">
        <div className="min-w-0">
          <p className="text-base font-medium">{t('출처 목록 보여 주기')}</p>
          <p className="text-sm text-faint">{t('답 끝에 근거로 쓴 문서·조항 목록을 붙입니다.')}</p>
        </div>
        <Switch checked={draft.showSources} onChange={(v) => set({ showSources: v })} label={t('출처 목록 보여 주기')} />
      </div>
      <div className="space-y-2 rounded-card border border-line px-3.5 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-base font-medium">{t('대화 열람')}</p>
            <p className="text-sm text-faint">
              {t('상담·민원처럼 대화 기록이 업무인 챗봇에만 켜세요. 켜면 방문자가 첫 질문 전에 동의하고, 그 뒤의 대화를 개인정보를 가린 채 볼 수 있습니다.')}
            </p>
          </div>
          <Switch
            checked={draft.conversationView === 'on'}
            onChange={(v) => set({ conversationView: v ? 'on' : 'off' })}
            label={t('대화 열람')}
          />
        </div>
        <p className="text-sm text-muted">
          {draft.conversationView === 'on'
            ? t('방문자가 동의한 뒤의 대화를 볼 수 있습니다.')
            : t('개별 대화는 보이지 않습니다. 사용량·자주 나온 주제·답하지 못한 질문·👍👎와 방문자가 보낸 대화만 보입니다.')}
        </p>
        {viewChanged && (
          <p className="text-sm text-warn">{t('저장하면 방문자가 다음 질문 전에 바뀐 안내를 다시 확인합니다.')}</p>
        )}
      </div>
      <div className="rounded-card border border-line">
        <button
          type="button"
          aria-expanded={limitsOpen}
          onClick={() => setLimitsOpen((v) => !v)}
          className="flex w-full items-center justify-between gap-2 px-3.5 py-3 text-left text-base font-medium"
        >
          {t('한도')}
          <ChevronDown size={15} className={cn('transition-transform', limitsOpen && 'rotate-180')} aria-hidden />
        </button>
        {limitsOpen && (
          <div className="grid gap-3 border-t border-line px-3.5 py-3 sm:grid-cols-2">
            {LIMIT_FIELDS.map(({ key, label }) => (
              <Field key={key} label={t(label)} hint={t('최대 {n}').replace('{n}', defaults[key].toLocaleString())}>
                <Input
                  type="number"
                  min={1}
                  max={defaults[key]}
                  value={draft.limits[key]}
                  onChange={(e) => {
                    const value = Math.max(1, Math.min(defaults[key], Number(e.target.value) || 1))
                    set({ limits: { ...draft.limits, [key]: value } })
                  }}
                />
              </Field>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
