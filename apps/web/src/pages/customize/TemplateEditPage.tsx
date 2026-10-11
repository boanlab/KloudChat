import { FileUp, Plus, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { GapQuestionsEditor } from '@/components/agents/GapQuestionsEditor'
import { fullSettings, ProcessSettingsEditor } from '@/components/agents/ProcessSettingsEditor'
import { Group } from '@/components/customize/AgentForm'
import { BackLink, HubTopBar } from '@/components/customize/DetailLayout'
import { PageBody } from '@/components/layout/AppShell'
import { Button, ConfirmDialog, Field, Input, LoadingState, RequiredNote, Select, Textarea } from '@/components/ui'
import { missingMessage } from '@/lib/formCheck'
import { errorCode, errorMessage, userTemplatesApi, type UserTemplateIn } from '@/lib/api'
import { useTemplateCatalogue } from '@/lib/templates'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { ProcessSettings, TemplateFormat, UserTemplate } from '@/types'

const NUMBERING_LABEL: Record<TemplateFormat['numbering'], string> = {
  decimal: '1. / 1.1',
  roman: 'I. / A.',
  korean: '1. / 가.',
  official: '1. / 가. / 1) / 가) (공문서)',
  none: '번호 없음',
}
const HEAD_LABEL: Record<TemplateFormat['head'], string> = {
  cover: '표지 한 쪽',
  header: '제목 아래 항목 표',
  memo: '제목 위 항목 줄 (회의록·공문)',
  press: '보도자료 (제목·부제·날짜줄)',
  paper: '논문 (저자·소속)',
  draft: '간이기안문 (결재란)',
}

const BLANK: UserTemplateIn = {
  name: '',
  description: '',
  category: '',
  fills: [],
  examplePrompt: '',
  instructions: '',
  checklist: '',
  genre: '',
  docFormat: { label: '', fields: [], numbering: 'decimal', head: 'header', lists: '', closing: false, subtitle: false },
  coverPage: false,
  bodyFace: '',
  checkSkills: ['gap-check', 'grounding-check'],
  checkOptions: [],
  questions: [],
  process: {},
  visibility: 'private',
}

/** A shipped template's fields as `/user-templates/start/{id}` sends them (snake_case). */
function fromStart(raw: Record<string, unknown>): UserTemplateIn {
  const fmt = (raw.doc_format ?? {}) as Partial<TemplateFormat>
  return {
    ...BLANK,
    name: String(raw.name ?? ''),
    description: String(raw.description ?? ''),
    category: String(raw.category ?? ''),
    fills: (raw.fills as string[]) ?? [],
    examplePrompt: String(raw.example_prompt ?? ''),
    instructions: String(raw.instructions ?? ''),
    checklist: String(raw.checklist ?? ''),
    genre: String(raw.genre ?? ''),
    docFormat: { ...BLANK.docFormat, ...fmt },
    coverPage: !!raw.cover_page,
    bodyFace: raw.body_face === 'design' ? 'design' : '',
    checkSkills: (raw.check_skills as string[]) ?? [],
    checkOptions: (raw.check_options as string[]) ?? [],
    questions: (raw.questions as UserTemplateIn['questions']) ?? [],
    process: (raw.process as Partial<ProcessSettings>) ?? {},
  }
}

function toIn(row: UserTemplate): UserTemplateIn {
  const { id: _id, formName: _f, hasForm: _h, mine: _m, ownerName: _o, updatedAt: _u, ...rest } = row
  return rest
}

const SAVE_ERRORS: Record<string, string> = {
  template_name: '템플릿 이름을 적으세요.',
  template_fields: '머리 항목은 열 개까지입니다.',
  process_outline: '고정 목차를 쓰려면 목차를 적으세요.',
  process_table: '필수 표마다 열 이름을 두 개 이상 적으세요.',
  template_form_type: 'DOCX 또는 HWPX 파일만 올릴 수 있습니다.',
  template_form_unreadable: '예시 문서를 읽지 못했습니다.',
}

/**
 * 템플릿 만들기·편집: every field a shipped document template sets, in one form. It is drawn on
 * the shared document page with the theme's colours and fonts; no HTML or CSS of its own.
 */
export function TemplateEditPage() {
  const t = useT()
  const navigate = useNavigate()
  const { templateId } = useParams()
  const [params] = useSearchParams()
  const catalogue = useTemplateCatalogue()
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const [value, setValue] = useState<UserTemplateIn | null>(templateId ? null : BLANK)
  const [row, setRow] = useState<UserTemplate | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const file = useRef<HTMLInputElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  const [nameError, setNameError] = useState<string | null>(null)
  const lang = useStore((s) => s.lang)
  const from = params.get('from')

  useEffect(() => {
    if (!templateId) return
    void userTemplatesApi.get(templateId).then((r) => {
      setRow(r)
      setValue(toIn(r))
    }).catch((err) => setError(errorMessage(err, t('템플릿을 불러오지 못했습니다.'))))
  }, [templateId, t])

  const startFrom = async (id: string) => {
    if (!id) return
    try {
      const raw = await userTemplatesApi.start(id)
      setValue((v) => ({ ...fromStart(raw), name: v?.name || '', visibility: v?.visibility ?? 'private' }))
    } catch (err) {
      setError(errorMessage(err, t('기본 템플릿을 불러오지 못했습니다.')))
    }
  }
  useEffect(() => {
    if (from && !templateId) void startFrom(from)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, templateId])

  if (!value || !catalogue) {
    return (
      <PageBody>
        {error ? <p role="alert" className="text-sm text-danger">{error}</p> : <LoadingState />}
      </PageBody>
    )
  }
  const mine = !row || row.mine
  const set = <K extends keyof UserTemplateIn>(key: K, v: UserTemplateIn[K]) => {
    setSaved(false)
    setValue({ ...value, [key]: v })
  }
  const setFormat = <K extends keyof TemplateFormat>(key: K, v: TemplateFormat[K]) =>
    set('docFormat', { ...value.docFormat, [key]: v })
  const settings = fullSettings(catalogue.process.defaults, value.process)
  const failed = (err: unknown, fallback: string) => {
    const said = SAVE_ERRORS[errorCode(err) ?? '']
    setError(said ? t(said) : errorMessage(err, `${t(fallback)} (${errorCode(err) ?? '?'})`))
  }

  const save = async () => {
    if (saving) return
    if (!value.name.trim()) {
      setNameError(missingMessage(lang, t('이름')))
      nameRef.current?.focus()
      return
    }
    setSaving(true)
    setError(null)
    try {
      const out = row ? await userTemplatesApi.update(row.id, value) : await userTemplatesApi.create(value)
      setRow(out)
      setSaved(true)
      void loadWorkspace()
      if (!row) navigate(`/customize/templates/${out.id}`, { replace: true })
    } catch (err) {
      failed(err, '템플릿을 저장하지 못했습니다.')
    } finally {
      setSaving(false)
    }
  }

  const title = row ? row.name : t('새 템플릿')
  return (
    <>
      <HubTopBar tab="agents" tabLabel="내 템플릿" to="/customize/templates" title={title} />
      <PageBody>
        <div className="mx-auto max-w-3xl">
          <BackLink tab="agents" label="내 템플릿" to="/customize/templates" />
          <h1 className="mb-1 text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="mb-6 text-base text-muted">
            {t('문서 템플릿은 무엇을 받을지, 어떤 형식과 순서로 쓸지, 쓰기 전후에 무엇을 확인할지를 정합니다. 모양은 테마의 색과 글꼴로 그립니다.')}
          </p>
          <form
            className="space-y-5"
            data-testid="template-form"
            onSubmit={(e) => {
              e.preventDefault()
              if (mine) void save()
            }}
          >
            {error && (
              <p role="alert" className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger">
                {error}
              </p>
            )}
            <RequiredNote />
            {!row && (
              <Field label={t('기본 템플릿에서 시작')} hint={t('고르면 그 템플릿의 모든 설정이 채워집니다. 고친 뒤 저장합니다.')}>
                <Select value="" onChange={(e) => void startFrom(e.target.value)}>
                  <option value="">{t('빈 템플릿')}</option>
                  {catalogue.starts.map((s) => (
                    <option key={s.id} value={s.id}>
                      {t(s.name)}
                    </option>
                  ))}
                </Select>
              </Field>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t('이름')} required error={nameError}>
                <Input ref={nameRef} value={value.name} maxLength={60}
                  onChange={(e) => {
                    setNameError(null)
                    set('name', e.target.value)
                  }}
                  placeholder={t('예: 우리 팀 주간 회의록')} />
              </Field>
              <Field label={t('분류')}>
                <Input value={value.category} maxLength={20} onChange={(e) => set('category', e.target.value)} placeholder={t('예: 업무')} />
              </Field>
            </div>
            <Field label={t('한 줄 설명')}>
              <Input value={value.description} maxLength={200} onChange={(e) => set('description', e.target.value)} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t('가져올 것')} hint={t('한 줄에 하나, 여섯 개까지. 카드에 보입니다.')}>
                <Textarea rows={3} value={value.fills.join('\n')}
                  onChange={(e) => set('fills', e.target.value.split('\n').slice(0, 6))} />
              </Field>
              <Field label={t('예시 요청')} hint={t('카드의 버튼이 입력창에 넣는 첫 문장')}>
                <Textarea rows={3} value={value.examplePrompt} onChange={(e) => set('examplePrompt', e.target.value)} />
              </Field>
            </div>

            <Group title={t('문서 형식')} hint={t('머리 항목, 번호, 머리 배치, 개조식, 「끝.」')} testId="template-format" open>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label={t('문서 종류 이름')} hint={t('머리에 보이는 이름. 비우면 템플릿 이름')}>
                  <Input value={value.docFormat.label} maxLength={30} onChange={(e) => setFormat('label', e.target.value)} />
                </Field>
                <Field label={t('머리 항목')} hint={t('한 줄에 하나, 열 개까지. 자료에 없으면 [항목] 빈칸')}>
                  {/* One line, prefixed 예:, so an empty field does not read as already filled. */}
                  <Textarea rows={4} value={value.docFormat.fields.join('\n')}
                    onChange={(e) => setFormat('fields', e.target.value.split('\n').slice(0, 10))}
                    placeholder={t('예: 회의명, 일시, 장소, 참석자를 한 줄에 하나씩')} />
                </Field>
                <Field label={t('번호 매기기')}>
                  <Select value={value.docFormat.numbering} onChange={(e) => setFormat('numbering', e.target.value as TemplateFormat['numbering'])}>
                    {catalogue.numberings.map((n) => <option key={n} value={n}>{t(NUMBERING_LABEL[n])}</option>)}
                  </Select>
                </Field>
                <Field label={t('머리 배치')}>
                  <Select value={value.docFormat.head} onChange={(e) => setFormat('head', e.target.value as TemplateFormat['head'])}>
                    {catalogue.heads.map((h) => <option key={h} value={h}>{t(HEAD_LABEL[h])}</option>)}
                  </Select>
                </Field>
              </div>
              <div className="flex flex-wrap gap-x-5 gap-y-1.5">
                {([
                  ['lists', '개조식 목록 (□ ○ - ㆍ)'],
                  ['closing', '끝에 「끝.」'],
                  ['subtitle', '부제'],
                ] as const).map(([key, label]) => (
                  <label key={key} className="flex items-center gap-2 text-base">
                    <input type="checkbox" className="size-4 accent-[var(--accent)]"
                      checked={key === 'lists' ? value.docFormat.lists === 'gaejo' : value.docFormat[key]}
                      onChange={(e) => (key === 'lists' ? setFormat('lists', e.target.checked ? 'gaejo' : '') : setFormat(key, e.target.checked))} />
                    {t(label)}
                  </label>
                ))}
                <label className="flex items-center gap-2 text-base">
                  <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.coverPage}
                    onChange={(e) => set('coverPage', e.target.checked)} />
                  {t('표지를 한 쪽으로')}
                </label>
                <label className="flex items-center gap-2 text-base">
                  <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.bodyFace === 'design'}
                    onChange={(e) => set('bodyFace', e.target.checked ? 'design' : '')} />
                  {t('본문 서체는 테마를 따름')}
                </label>
              </div>
            </Group>

            <Group title={t('쓰는 법')} hint={t('지침, 장르 규칙, 점검표')} testId="template-writing">
              <Field label={t('지침')} hint={t('쓰는 모델이 따를 규칙입니다. 독자에게는 보이지 않습니다.')}>
                <Textarea rows={6} value={value.instructions} onChange={(e) => set('instructions', e.target.value)} />
              </Field>
              <Field label={t('장르 규칙')} hint={t('개요와 본문에 함께 주는 형식 규칙')}>
                <Textarea rows={3} value={value.genre} onChange={(e) => set('genre', e.target.value)} />
              </Field>
              <Field label={t('점검표')} hint={t('한 줄에 하나. 「- 」로 시작')}>
                <Textarea rows={3} value={value.checklist} onChange={(e) => set('checklist', e.target.value)} />
              </Field>
            </Group>

            <Group title={t('빠진 정보 질문')} hint={t('쓰기 전에 자료에 없으면 물어볼 것')} testId="template-questions">
              <GapQuestionsEditor value={value.questions} detectors={catalogue.detectors} onChange={(next) => set('questions', next)} />
            </Group>

            <Group title={t('검사')} hint={t('근거 확인, 빠진 정보 묻기')} testId="template-checks">
              <ul className="space-y-1.5">
                {catalogue.checkSkills.map((c) => (
                  <li key={c.key}>
                    <label className="flex items-center gap-2 text-base">
                      <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.checkSkills.includes(c.key)}
                        onChange={() => set('checkSkills', value.checkSkills.includes(c.key)
                          ? value.checkSkills.filter((k) => k !== c.key) : [...value.checkSkills, c.key])} />
                      {t(c.name)}
                    </label>
                  </li>
                ))}
                <li>
                  <label className="flex items-center gap-2 text-base">
                    <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={value.checkOptions.includes('titles')}
                      onChange={(e) => set('checkOptions', e.target.checked ? [...value.checkOptions, 'titles'] : value.checkOptions.filter((k) => k !== 'titles'))} />
                    {t('직함·제품명까지 근거 확인')}
                  </label>
                </li>
              </ul>
            </Group>

            <Group title={t('세부 설정')} hint={t('계산·검사·검토 규칙·구조')} testId="template-settings">
              <ProcessSettingsEditor value={settings} catalogue={catalogue.process}
                onChange={(next) => set('process', next)} />
            </Group>

            <Group title={t('예시 문서')} hint={row?.hasForm ? row.formName : t('DOCX·HWPX 문서를 참고 글로')} testId="template-form-file">
              <p className="text-sm text-muted">
                {t('올린 문서의 글(제목·칸 이름)을 쓰는 모델이 참고합니다. 내보내는 파일의 모양은 테마가 정합니다.')}
              </p>
              {row ? (
                <div className="flex flex-wrap items-center gap-2">
                  <input ref={file} type="file" accept=".docx,.hwpx" className="hidden" aria-label={t('예시 문서')}
                    onChange={async (e) => {
                      const picked = e.target.files?.[0]
                      if (!picked) return
                      try {
                        setRow(await userTemplatesApi.uploadForm(row.id, picked))
                      } catch (err) {
                        failed(err, '예시 문서를 올리지 못했습니다.')
                      }
                    }} />
                  <Button type="button" size="sm" disabled={!mine} onClick={() => file.current?.click()}>
                    <FileUp size={13} />
                    {row.hasForm ? t('다른 파일로 바꾸기') : t('예시 문서 올리기')}
                  </Button>
                  {row.hasForm && (
                    <Button type="button" size="sm" variant="ghost" disabled={!mine}
                      onClick={async () => setRow(await userTemplatesApi.removeForm(row.id))}>
                      {t('예시 문서 빼기')}
                    </Button>
                  )}
                </div>
              ) : (
                <p className="text-sm text-faint">{t('저장한 뒤 올릴 수 있습니다.')}</p>
              )}
            </Group>

            <Group title={t('공유')} hint={value.visibility === 'org' ? t('모두에게 공개') : t('나만 쓰기')}>
              <Select value={value.visibility} onChange={(e) => set('visibility', e.target.value as UserTemplateIn['visibility'])} aria-label={t('공유')}>
                <option value="private">{t('나만 쓰기')}</option>
                <option value="org">{t('모두에게 공개 (다른 사람은 고르기만)')}</option>
              </Select>
            </Group>

            <div className="sticky bottom-0 z-10 -mx-4 flex items-center justify-end gap-2 border-t border-line bg-bg/95 px-4 py-3 sm:-mx-6 sm:px-6">
              {saved && <span role="status" className="mr-auto text-sm text-success">{t('저장했습니다.')}</span>}
              {row && mine && (
                <Button type="button" variant="ghost" onClick={() => setConfirming(true)}>
                  <Trash2 size={14} />
                  {t('삭제')}
                </Button>
              )}
              <Button type="button" onClick={() => navigate(-1)}>{t('취소')}</Button>
              <Button type="submit" variant="primary" disabled={!mine} aria-busy={saving || undefined}>
                {saving ? t('저장 중…') : row ? t('저장') : t('만들기')}
              </Button>
            </div>
          </form>
          <ConfirmDialog
            open={confirming}
            onClose={() => setConfirming(false)}
            onConfirm={async () => {
              if (!row) return
              await userTemplatesApi.remove(row.id)
              void loadWorkspace()
              navigate('/customize/templates', { replace: true })
            }}
            title={t('{name} 삭제').replace('{name}', row?.name ?? '')}
            description={t('이 템플릿을 쓰던 에이전트는 템플릿 없이 일합니다. 이미 만든 아티팩트는 기본 템플릿으로 열립니다.')}
          />
        </div>
      </PageBody>
    </>
  )
}

/** /customize/templates — the person's templates and the ones shared with them. */
export function TemplatesPage() {
  const t = useT()
  const [rows, setRows] = useState<{ mine: UserTemplate[]; shared: UserTemplate[] } | null>(null)
  useEffect(() => {
    void userTemplatesApi.list().then(setRows).catch(() => setRows({ mine: [], shared: [] }))
  }, [])
  const list = (items: UserTemplate[], label: string) => (
    <section className="space-y-2" aria-label={t(label)}>
      <h2 className="text-base font-medium">{t(label)}</h2>
      {items.length === 0 ? (
        <p className="text-sm text-faint">{t('아직 없습니다.')}</p>
      ) : (
        <ul className="divide-y divide-line rounded-control border border-line">
          {items.map((row) => (
            <li key={row.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
              <span className="min-w-0">
                <Link to={`/customize/templates/${row.id}`} className="font-medium text-fg hover:underline">{row.name}</Link>
                <span className="block truncate text-sm text-muted">{row.description || row.docFormat.label}</span>
              </span>
              {!row.mine && <span className="shrink-0 text-sm text-faint">{row.ownerName}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
  return (
    <>
      <HubTopBar tab="agents" tabLabel="에이전트" title={t('내 템플릿')} />
      <PageBody>
        <div className="mx-auto max-w-3xl space-y-6">
          <div className="flex items-center justify-between gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{t('내 템플릿')}</h1>
            <Link to="/customize/templates/new" className="inline-flex items-center gap-1 text-base text-accent hover:underline">
              <Plus size={14} />
              {t('새 템플릿')}
            </Link>
          </div>
          {rows ? (
            <>
              {list(rows.mine, '내가 만든 템플릿')}
              {list(rows.shared, '공유된 템플릿')}
            </>
          ) : (
            <LoadingState />
          )}
        </div>
      </PageBody>
    </>
  )
}
