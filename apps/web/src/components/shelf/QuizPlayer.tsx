import { Check, Download, FileText, RotateCcw, Shuffle, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Markdown } from '@/components/chat/Markdown'
import { Badge, Button, Dropdown, Input, MenuItem, Modal } from '@/components/ui'
import { errorMessage, shelfApi } from '@/lib/api'
import { sameAnswer, saveBlob, shuffled, type QuizQuestion, type StudioItem } from '@/lib/shelf'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { CitationLayer } from './CitationLayer'

const CIRCLED = ['①', '②', '③', '④', '⑤']

/** Explanation with its citations as source buttons. */
export function Explained({ item, text, cite }: { item: StudioItem; text: string; cite: number[] }) {
  const sources = item.content?.sources ?? []
  const listed = sources.filter((s) => cite.includes(s.n) || text.includes(`[${s.n}]`))
  const body = listed.length ? `${text}\n\n### 출처\n${listed.map((s) => `- [${s.n}] ${s.name}`).join('\n')}` : text
  return (
    <CitationLayer projectId={item.projectId}>
      <Markdown className="text-base">{body}</Markdown>
    </CitationLayer>
  )
}

/** Downloads of a set: CSV, Anki text, and (quizzes) a 한글/Word sheet in the document panel. */
export function SetExports({ item, onError }: { item: StudioItem; onError: (message: string) => void }) {
  const t = useT()
  const navigate = useNavigate()
  const download = async (format: 'csv' | 'anki') => {
    try {
      const blob = await shelfApi.exportSet(item.id, format)
      saveBlob(blob, `${item.title || item.kind}.${format === 'csv' ? 'csv' : 'txt'}`)
    } catch (err) {
      onError(errorMessage(err, t('내려받지 못했습니다.')))
    }
  }
  const sheet = async (format: 'hwp' | 'docx' | 'web') => {
    try {
      const made = await shelfApi.sheet(item.id, format)
      navigate(`/s/${made.sessionId}?artifact=${made.artifactId}`)
    } catch (err) {
      onError(errorMessage(err, t('시험지를 만들지 못했습니다.')))
    }
  }
  return (
    <Dropdown
      align="right"
      trigger={() => (
        <Button size="sm">
          <Download size={13} />
          {t('내보내기')}
        </Button>
      )}
    >
      <MenuItem icon={<Download size={14} />} onClick={() => void download('csv')}>{t('CSV (엑셀)')}</MenuItem>
      <MenuItem icon={<Download size={14} />} onClick={() => void download('anki')}>{t('Anki 가져오기용 텍스트')}</MenuItem>
      {item.kind === 'quiz' && (
        <>
          <MenuItem icon={<FileText size={14} />} onClick={() => void sheet('hwp')}>{t('한글 시험지')}</MenuItem>
          <MenuItem icon={<FileText size={14} />} onClick={() => void sheet('docx')}>{t('Word 시험지')}</MenuItem>
          <MenuItem icon={<FileText size={14} />} onClick={() => void sheet('web')}>{t('웹 보고서 시험지')}</MenuItem>
        </>
      )}
    </Dropdown>
  )
}

type Answer = { given: string; right: boolean }

/** A quiz played one question at a time: score, explanations with sources, retry the wrong
 * ones, shuffle. */
export function QuizPlayer({ item, onClose }: { item: StudioItem | null; onClose: () => void }) {
  const t = useT()
  const all = useMemo(() => item?.content?.questions ?? [], [item])
  const [order, setOrder] = useState<QuizQuestion[] | null>(null)
  const [at, setAt] = useState(0)
  const [answers, setAnswers] = useState<Record<string, Answer>>({})
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)
  if (!item) return null
  const deck = order ?? all
  const done = at >= deck.length
  const question = deck[at]
  const answered = question ? answers[question.id] : undefined
  const score = deck.filter((q) => answers[q.id]?.right).length

  const restart = (next: QuizQuestion[]) => {
    setOrder(next)
    setAt(0)
    setAnswers({})
    setTyped('')
  }
  const answer = (given: string, right: boolean) => {
    if (!question || answered) return
    setAnswers((current) => ({ ...current, [question.id]: { given, right } }))
  }

  return (
    <Modal open onClose={onClose} title={item.title} width="max-w-2xl">
      <div className="flex flex-wrap items-center gap-2" data-testid="quiz-player">
        <Badge tone="accent">
          {done ? t('결과') : t('{n}/{total}번').replace('{n}', String(at + 1)).replace('{total}', String(deck.length))}
        </Badge>
        <span className="text-sm text-muted" data-testid="quiz-score">
          {t('맞힘 {right}/{total}').replace('{right}', String(score)).replace('{total}', String(deck.length))}
        </span>
        <span className="ml-auto flex gap-1.5">
          <Button size="sm" variant="ghost" onClick={() => restart(shuffled(all))}>
            <Shuffle size={13} />
            {t('섞기')}
          </Button>
          <SetExports item={item} onError={setError} />
        </span>
      </div>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {!done && question && (
        <div className="space-y-3">
          <p className="text-md font-medium">{question.question}</p>
          {question.type === 'mc' ? (
            <div className="space-y-1.5">
              {(question.choices ?? []).map((choice, i) => {
                const isAnswer = i === question.answer
                const chosen = answered?.given === String(i)
                return (
                  <button
                    key={choice}
                    type="button"
                    disabled={!!answered}
                    onClick={() => answer(String(i), isAnswer)}
                    className={cn(
                      'flex w-full items-start gap-2 rounded-control border px-3 py-2 text-left text-base disabled:cursor-default',
                      !answered && 'border-line hover:border-accent hover:bg-accent-soft',
                      answered && isAnswer && 'border-success bg-success/10',
                      answered && chosen && !isAnswer && 'border-danger bg-danger/10',
                      answered && !chosen && !isAnswer && 'border-line opacity-60',
                    )}
                  >
                    <span className="shrink-0">{CIRCLED[i]}</span>
                    <span>{choice}</span>
                  </button>
                )
              })}
            </div>
          ) : (
            <form
              className="flex gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                if (typed.trim()) answer(typed.trim(), sameAnswer(typed, String(question.answer)))
              }}
            >
              <Input
                aria-label={t('답')}
                value={typed}
                disabled={!!answered}
                onChange={(e) => setTyped(e.target.value)}
                placeholder={t('답을 쓰세요')}
              />
              <Button type="submit" disabled={!!answered || !typed.trim()}>{t('확인')}</Button>
            </form>
          )}
          {answered && (
            <div className="space-y-2 rounded-control border border-line bg-elevated px-3 py-2.5" data-testid="quiz-feedback">
              <p className={cn('flex items-center gap-1.5 font-medium', answered.right ? 'text-success' : 'text-danger')}>
                {answered.right ? <Check size={14} /> : <X size={14} />}
                {answered.right ? t('맞았습니다') : t('틀렸습니다')}
                {question.type === 'short' && (
                  <span className="font-normal text-muted">
                    {t('정답: {answer}').replace('{answer}', String(question.answer))}
                  </span>
                )}
              </p>
              <Explained item={item} text={question.explanation} cite={question.cite} />
              {question.type === 'short' && !answered.right && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setAnswers((current) => ({ ...current, [question.id]: { ...answered, right: true } }))}
                >
                  {t('맞은 것으로 치기')}
                </Button>
              )}
            </div>
          )}
          <div className="flex justify-between">
            <Button variant="ghost" disabled={at === 0} onClick={() => { setAt(at - 1); setTyped('') }}>{t('이전')}</Button>
            <Button variant="primary" disabled={!answered} onClick={() => { setAt(at + 1); setTyped('') }}>
              {at + 1 === deck.length ? t('결과 보기') : t('다음')}
            </Button>
          </div>
        </div>
      )}
      {done && (
        <div className="space-y-3 text-center" data-testid="quiz-result">
          <p className="text-xl font-semibold">
            {t('{right}/{total} 맞혔습니다').replace('{right}', String(score)).replace('{total}', String(deck.length))}
          </p>
          <div className="flex flex-wrap justify-center gap-2">
            <Button
              variant="primary"
              disabled={score === deck.length}
              onClick={() => restart(deck.filter((q) => !answers[q.id]?.right))}
            >
              <RotateCcw size={14} />
              {t('틀린 문제만 다시')}
            </Button>
            <Button onClick={() => restart(all)}>{t('처음부터')}</Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
