import { CircleCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Field, Textarea } from '@/components/ui'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

const LIMIT = 1500

/**
 * 개인 맞춤 설정 — the two paragraphs every conversation starts with.
 *
 * What the person wants the model to know about them, and how they want
 * answers written. Stored on the account with the other preferences and put
 * first in the context of every chat; a document takes only the second half,
 * so a line about oneself never becomes a report's subject.
 */
export function PersonalizationTab() {
  const t = useT()
  const { user, updateProfile } = useStore()
  const prefs = user?.preferences
  const [aboutMe, setAboutMe] = useState('')
  const [responseStyle, setResponseStyle] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setAboutMe(prefs?.aboutMe ?? '')
    setResponseStyle(prefs?.responseStyle ?? '')
  }, [prefs?.aboutMe, prefs?.responseStyle])

  const dirty =
    aboutMe !== (prefs?.aboutMe ?? '') || responseStyle !== (prefs?.responseStyle ?? '')

  const save = async () => {
    setBusy(true)
    setSaved(false)
    try {
      await updateProfile({ preferences: { aboutMe, responseStyle } })
      setSaved(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-base font-semibold">{t('개인 맞춤 설정')}</h2>
        <p className="text-sm text-muted">
          {t('여기 적은 것은 모든 대화의 맨 앞에 들어갑니다. 에이전트나 프로젝트의 지침이 있으면 그쪽이 우선합니다.')}
        </p>
      </div>

      <Field
        label={t('나에 대해 알아 두면 좋은 것')}
        hint={t('하는 일, 전공, 관심사, 자주 쓰는 도구 — 답을 맞추는 데 참고합니다. 대화에서만 쓰이고 보고서·슬라이드에는 들어가지 않습니다.')}
      >
        <Textarea
          value={aboutMe}
          onChange={(e) => setAboutMe(e.target.value.slice(0, LIMIT))}
          rows={6}
          placeholder={t('예: 컴퓨터공학과 3학년. 웹 개발을 배우는 중이고 JavaScript 를 주로 쓴다. 영어 자료는 한국어로 풀어 주면 좋겠다.')}
        />
        <p className="mt-1 text-right text-xs text-faint">{aboutMe.length}/{LIMIT}</p>
      </Field>

      <Field
        label={t('답변 방식')}
        hint={t('말투, 길이, 형식, 언어 — 대화와 문서 모두에 적용됩니다.')}
      >
        <Textarea
          value={responseStyle}
          onChange={(e) => setResponseStyle(e.target.value.slice(0, LIMIT))}
          rows={6}
          placeholder={t('예: 결론부터, 짧게. 존댓말. 코드는 설명보다 예제 위주로. 확실하지 않으면 그렇다고 말할 것.')}
        />
        <p className="mt-1 text-right text-xs text-faint">{responseStyle.length}/{LIMIT}</p>
      </Field>

      <div className="flex items-center gap-3">
        <Button
          variant="primary"
          disabled={busy || !dirty}
          title={!dirty ? t('바뀐 내용이 없습니다') : busy ? t('저장 중…') : undefined}
          onClick={() => void save()}
        >
          {t('저장')}
        </Button>
        {saved && !dirty && (
          <span className="flex items-center gap-1.5 text-sm text-success">
            <CircleCheck size={14} />
            {t('저장했습니다. 다음 대화부터 적용됩니다.')}
          </span>
        )}
      </div>
    </div>
  )
}
