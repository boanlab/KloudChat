import { AlertTriangle, FileArchive, FileText, ShieldCheck, Upload, X, XCircle } from 'lucide-react'
import { useRef, useState, type DragEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { BackLink, HubTopBar } from '@/components/customize/DetailLayout'
import { ScriptRow } from '@/components/customize/SkillServerFields'
import { Badge, Button, LoadingState } from '@/components/ui'
import { errorMessage, skillsApi, type SkillImportFinding, type SkillImportPreview } from '@/lib/api'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'

const ACCEPT = '.md,.zip,.skill'
const MAX_FILES = 10

/** Screen words per finding code; the server's own sentence is the fallback. */
const FINDING_TEXT: Record<string, string> = {
  file_too_large: '파일이 2MB를 넘습니다.',
  not_utf8: 'UTF-8 텍스트가 아닙니다.',
  unsupported_type: '.md, .zip, .skill 파일만 올릴 수 있습니다.',
  front_matter: '맨 앞에 --- 로 감싼 YAML 머리(name, description)가 필요합니다.',
  missing_fields: 'YAML 머리에 name 과 description 이 모두 있어야 합니다.',
  too_long: '이름이나 설명이 너무 깁니다.',
  empty_body: 'SKILL.md 본문(절차)이 비어 있습니다.',
  body_too_long: '본문이 24,000자를 넘습니다.',
  bad_archive: 'zip 파일을 열 수 없습니다.',
  too_many_entries: '압축 안 항목이 50개를 넘습니다.',
  unsafe_path: '압축 안에 절대 경로나 .. 가 든 경로가 있습니다.',
  symlink: '압축 안에 심볼릭 링크가 있습니다.',
  encrypted: '암호가 걸린 압축은 읽지 않습니다.',
  too_large: '압축을 푼 크기가 2MB를 넘습니다.',
  compression_ratio: '압축률이 비정상적으로 높습니다(압축 폭탄 의심).',
  nested_archive: '압축 안에 또 다른 압축 파일이 있습니다.',
  no_skill_md: '압축 안에 SKILL.md 가 없습니다.',
  multiple_skill_md: '압축 안에 SKILL.md 가 여러 개입니다. 하나만 넣어 주세요.',
  skill_md_not_at_root: 'SKILL.md 는 압축 맨 위나 폴더 하나 바로 안에 있어야 합니다.',
  server_field_ignored: '카탈로그 전용 항목(status)은 무시했어요.',
  unknown_field: '모르는 YAML 항목은 무시했어요.',
  unknown_kind: '모르는 kinds 값은 뺐어요.',
  script_tool_dropped: '실행할 스크립트가 없어 run_skill_script 를 뺐어요.',
  unknown_tools: '모르는 도구는 뺐어요.',
  scripts_excluded: '파이썬이 아닌 스크립트와 scripts/ 밖의 스크립트는 제외했어요.',
  script_network: '네트워크에 접속하는 모듈을 씁니다. 샌드박스에서는 외부로 나가지 못할 수 있습니다.',
  script_process: '다른 프로그램을 실행합니다.',
  script_file_write: '작업 폴더 밖의 경로에 쓰거나 지웁니다.',
  script_dynamic_code: '실행 중에 만든 코드를 돌립니다.',
  script_syntax: '파이썬 문법 오류가 있어 실행할 수 없습니다.',
  script_name: '스크립트 이름은 영문 소문자·숫자·_ 로 40자까지입니다.',
  too_many_scripts: '스크립트는 8개까지 올릴 수 있습니다.',
  script_too_large: '스크립트 하나는 64KB까지입니다.',
  skill_stage_unknown: 'stage 는 pre 또는 post 만 쓸 수 있어 뺐어요.',
  skill_entry_unknown: '기본 제공 검사가 아닌 entry 는 뺐어요.',
  skill_entry_without_stage: 'stage 없는 entry 는 뺐어요.',
  skill_post_check_unknown: '기본 제공 스크립트가 아닌 post_check 는 뺐어요.',
  skill_post_check_own_script: '직접 올린 스크립트는 post_check 로 쓸 수 없어 뺐어요. run_skill_script 로 샌드박스에서만 실행됩니다.',
  skill_script_unknown: '모르는 스크립트 이름은 uses 에서 뺐어요.',
  skill_copy_kind_unknown: '모르는 copy_kinds 값은 뺐어요.',
  resources_skipped: '본문 한도를 넘어 일부 참고 자료는 넣지 못했어요.',
  instruction_override: '이전 지시를 무시하라는 식의 문장이 있습니다.',
  data_exfiltration: '자료를 외부 주소나 메일로 보내라는 지시가 있습니다.',
  hidden_characters: '보이지 않는 방향 제어·폭 없는 문자가 있습니다.',
  encoded_blob: '긴 base64 인코딩 덩어리가 있습니다.',
  secret_like: 'API 키·토큰처럼 보이는 문자열이 있습니다.',
}

const EXCLUDED_TEXT: Record<string, string> = {
  script: '파이썬이 아닌 스크립트 · 제외',
  binary: '텍스트가 아님',
  unused: '쓰지 않는 파일',
  too_long: '본문 한도 초과',
}

const fileKey = (file: File) => `${file.name}:${file.size}:${file.lastModified}`

/** /customize/skills/upload — SKILL.md, .zip or .skill files, previewed and scanned before they are kept. */
export function SkillUploadPage() {
  const t = useT()
  const navigate = useNavigate()
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const setToast = useStore((s) => s.setToast)
  const input = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [previews, setPreviews] = useState<SkillImportPreview[]>([])
  const [checking, setChecking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [draft, setDraft] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [over, setOver] = useState(false)

  const check = async (next: File[]) => {
    setFiles(next)
    setPreviews([])
    setError(null)
    if (next.length === 0) return
    setChecking(true)
    try {
      const result = await skillsApi.importFiles(next, { dryRun: true })
      setPreviews(result.items)
    } catch (err) {
      setError(errorMessage(err, t('파일을 확인하지 못했습니다.')))
    } finally {
      setChecking(false)
    }
  }

  const add = (picked: FileList | File[] | null) => {
    if (!picked) return
    const seen = new Set(files.map(fileKey))
    const fresh = Array.from(picked).filter((file) => !seen.has(fileKey(file)))
    if (fresh.length === 0) return
    const next = [...files, ...fresh]
    if (next.length > MAX_FILES) {
      setError(t('한 번에 {n}개까지 올릴 수 있습니다.').replace('{n}', String(MAX_FILES)))
      return
    }
    void check(next)
  }

  // Dropping a file only removes its own card; nothing is asked of the server again.
  const remove = (index: number) => {
    setFiles((list) => list.filter((_, i) => i !== index))
    setPreviews((list) => list.filter((_, i) => i !== index))
    setError(null)
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setOver(false)
    add(event.dataTransfer.files)
  }

  const blocked = previews.some((p) => !p.ok)
  const ready = files.length > 0 && previews.length === files.length && !blocked && !checking

  const upload = async () => {
    setSaving(true)
    setError(null)
    try {
      const result = await skillsApi.importFiles(files, { draft })
      await loadWorkspace()
      const made = result.created
      setToast({
        text:
          made.length === 1
            ? t('「{name}」 스킬을 올렸습니다.').replace('{name}', made[0].name)
            : t('스킬 {n}개를 올렸습니다.').replace('{n}', String(made.length)),
      })
      navigate(made.length === 1 ? `/customize/skills/${made[0].id}` : '/customize?tab=skills&view=mine', { replace: true })
    } catch (err) {
      setError(errorMessage(err, t('올리지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <HubTopBar tab="skills" tabLabel="스킬" title={t('스킬 업로드')} />
      <PageBody>
        <div className="mx-auto max-w-3xl pb-24" data-testid="skill-upload">
          <BackLink tab="skills" label="스킬" />
          <h1 className="mb-1 text-2xl font-semibold tracking-tight">{t('스킬 업로드')}</h1>
          <p className="mb-6 text-base text-muted">{t('다른 곳에서 만든 스킬 파일을 내 스킬로 가져옵니다.')}</p>

          <div
            data-testid="skill-drop"
            onDragOver={(event) => {
              event.preventDefault()
              setOver(true)
            }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
            className={cn(
              'flex flex-col items-center gap-3 rounded-card border-2 border-dashed px-5 py-10 text-center transition-colors max-sm:py-8',
              over ? 'border-accent bg-accent-soft' : 'border-line-strong bg-panel',
            )}
          >
            <Upload size={22} className="text-faint" aria-hidden />
            <p className="text-base text-fg">{t('스킬 파일을 여기로 끌어다 놓거나 「찾아보기」를 누르세요')}</p>
            <p className="text-sm text-muted">{t('여러 개를 한 번에 추가할 수 있습니다.')}</p>
            <Button onClick={() => input.current?.click()}>{t('찾아보기')}</Button>
            <input
              ref={input}
              type="file"
              multiple
              accept={ACCEPT}
              aria-label={t('스킬 파일 고르기')}
              className="sr-only"
              onChange={(event) => {
                add(event.target.files)
                event.target.value = ''
              }}
            />
          </div>

          <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-muted">
            <li>{t('.md 파일은 맨 앞 YAML 머리에 스킬 이름(name)과 설명(description)이 있어야 합니다.')}</li>
            <li>{t('.zip 또는 .skill 파일에는 SKILL.md 가 들어 있어야 합니다.')}</li>
          </ul>

          <div className="mt-5 flex items-start gap-3 rounded-card border border-line bg-panel px-4 py-3">
            <ShieldCheck size={18} className="mt-0.5 shrink-0 text-success" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="flex flex-wrap items-center gap-x-2 text-base font-medium text-fg">
                {t('보안 스캔')}
                <span className="text-sm font-normal text-muted">{t('업로드할 때 실행')}</span>
              </p>
              <p className="mt-0.5 text-sm text-muted">
                {t('경로·크기·압축 폭탄을 검사하고, 지시 무시·외부 전송·숨은 문자·키처럼 보이는 문자열을 찾습니다. scripts/ 의 파이썬 스크립트는 네트워크·다른 프로그램 실행·밖의 파일 쓰기를 따로 살피고, 코드 실행 샌드박스에서만 돌립니다. 검사는 휴리스틱이라 모든 위험을 찾지는 못합니다.')}
              </p>
            </div>
          </div>

          {checking && (
            <div className="mt-6">
              <LoadingState label={t('파일을 확인하는 중…')} />
            </div>
          )}

          {!checking && files.length > 0 && (
            <ul className="mt-6 space-y-3" aria-label={t('올릴 파일')}>
              {files.map((file, index) => (
                <PreviewCard key={fileKey(file)} file={file} preview={previews[index]} onRemove={() => remove(index)} />
              ))}
            </ul>
          )}

          {error && (
            <p role="alert" className="mt-4 rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger">
              {error}
            </p>
          )}

          <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-line pt-4">
            <label className="flex min-w-0 flex-1 items-start gap-2 text-base">
              <input
                type="checkbox"
                className="mt-1 size-4 shrink-0 accent-[var(--color-accent)]"
                checked={draft}
                onChange={(event) => setDraft(event.target.checked)}
              />
              <span>
                {t('초안으로 올리기')}
                <span className="block text-sm text-muted">{t('켜기 전까지 입력창의 스킬 목록에 나오지 않습니다.')}</span>
              </span>
            </label>
            <div className="ml-auto flex gap-2">
              <Button onClick={() => navigate('/customize?tab=skills')}>{t('취소')}</Button>
              <Button variant="primary" disabled={!ready || saving} onClick={() => void upload()}>
                <Upload size={15} />
                {saving ? t('올리는 중…') : t('업로드')}
              </Button>
            </div>
            {blocked && <p className="w-full text-sm text-danger">{t('막힌 파일을 빼야 올릴 수 있습니다.')}</p>}
          </div>
        </div>
      </PageBody>
    </>
  )
}

function PreviewCard({ file, preview, onRemove }: { file: File; preview?: SkillImportPreview; onRemove: () => void }) {
  const t = useT()
  const archive = /\.(zip|skill)$/i.test(file.name)
  const blocks = preview?.findings.filter((f) => f.severity === 'block') ?? []
  const warns = preview?.findings.filter((f) => f.severity === 'warn') ?? []
  return (
    <li
      data-testid="skill-preview"
      className={cn('rounded-card border bg-panel px-4 py-3', preview && !preview.ok ? 'border-danger/40' : 'border-line')}
    >
      <div className="flex items-start gap-2.5">
        {archive ? (
          <FileArchive size={17} className="mt-0.5 shrink-0 text-faint" aria-hidden />
        ) : (
          <FileText size={17} className="mt-0.5 shrink-0 text-faint" aria-hidden />
        )}
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 truncate font-mono text-sm text-muted">{file.name}</span>
            {preview &&
              (preview.ok ? (
                <Badge tone={warns.length ? 'warn' : 'success'}>
                  {warns.length ? t('주의 {n}건').replace('{n}', String(warns.length)) : t('문제 없음')}
                </Badge>
              ) : (
                <Badge tone="danger">{t('올릴 수 없음')}</Badge>
              ))}
          </p>
          {preview?.name && <p className="mt-1 text-md font-semibold text-fg">{preview.name}</p>}
          {preview?.description && <p className="mt-0.5 text-base text-muted">{preview.description}</p>}
          {preview?.ok && (
            <p className="mt-1.5 flex flex-wrap gap-1.5">
              <Badge>v{preview.version}</Badge>
              <Badge>{t('약 {n} 토큰').replace('{n}', preview.estimatedTokens.toLocaleString())}</Badge>
              {preview.requiredTools.map((tool) => (
                <Badge key={tool} className="font-mono">
                  {tool}
                </Badge>
              ))}
            </p>
          )}
        </div>
        <Button variant="ghost" size="icon" aria-label={t('{name} 빼기').replace('{name}', file.name)} onClick={onRemove}>
          <X size={15} />
        </Button>
      </div>

      {preview && (blocks.length > 0 || warns.length > 0) && (
        <ul className="mt-3 space-y-1.5" aria-label={t('검사 결과')}>
          {[...blocks, ...warns].map((finding, i) => (
            <FindingRow key={`${finding.code}:${i}`} finding={finding} />
          ))}
        </ul>
      )}

      {preview?.server && <ServerSummary server={preview.server} />}

      {preview && (preview.scripts?.length ?? 0) > 0 && (
        <div className="mt-3 space-y-1.5" data-testid="skill-preview-scripts">
          <p className="text-sm font-medium text-muted">
            {t('스크립트 {n}개 · 코드 실행 샌드박스에서만 실행').replace('{n}', String(preview.scripts?.length ?? 0))}
          </p>
          <ul className="space-y-1.5">
            {preview.scripts?.map((script) => (
              <ScriptRow key={script.name} script={script} />
            ))}
          </ul>
          <p className="text-sm text-faint">{t('스크립트가 있는 스킬은 관리자가 승인한 뒤에 다른 사람에게 공유됩니다.')}</p>
        </div>
      )}

      {preview && archive && (preview.kept.length > 0 || preview.excluded.length > 0) && (
        <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
          <div className="min-w-0">
            <p className="mb-1 font-medium text-muted">{t('가져올 파일')}</p>
            <ul className="space-y-0.5">
              {preview.kept.map((path) => (
                <li key={path} className="truncate font-mono text-fg">
                  {path}
                </li>
              ))}
              {preview.kept.length === 0 && <li className="text-faint">{t('없음')}</li>}
            </ul>
          </div>
          <div className="min-w-0">
            <p className="mb-1 font-medium text-muted">{t('제외한 파일')}</p>
            <ul className="space-y-0.5">
              {preview.excluded.map((item) => (
                <li key={item.path} className="flex min-w-0 items-baseline gap-2">
                  <span className="min-w-0 truncate font-mono text-muted line-through decoration-faint">{item.path}</span>
                  <span className="shrink-0 text-xs text-faint">{t(EXCLUDED_TEXT[item.reason] ?? item.reason)}</span>
                </li>
              ))}
              {preview.excluded.length === 0 && <li className="text-faint">{t('없음')}</li>}
            </ul>
          </div>
        </div>
      )}
    </li>
  )
}

/** What the server would run for the skill, as its SKILL.md asked and the allowlists kept. */
function ServerSummary({ server }: { server: NonNullable<SkillImportPreview['server']> }) {
  const t = useT()
  const rows: [string, string][] = []
  if (server.postCheck.length) rows.push([t('답 점검 스크립트'), server.postCheck.join(', ')])
  if (server.postJudge) rows.push([t('자료에 없는 문장도 모델이 찾기'), typeof server.postJudge === 'string' ? server.postJudge : t('켜짐')])
  if (server.copyCheck) rows.push([t('카피로 점검하기'), server.copyKinds.join(', ') || t('모두')])
  if (server.tables) rows.push([t('비교표 칸마다 출처 확인'), t('켜짐')])
  if (server.stage) rows.push([t('검사 스킬로 쓰기'), `${server.stage} · ${server.entry}`])
  if (server.uses.length) rows.push([t('스킬이 실행할 스크립트'), server.uses.join(', ')])
  return (
    <div className="mt-3 rounded-control border border-line px-3 py-2 text-sm" data-testid="skill-preview-server">
      <p className="mb-1 font-medium text-muted">{t('서버가 돌리는 점검')}</p>
      <dl className="grid gap-x-3 gap-y-0.5 sm:grid-cols-[auto_1fr]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-faint">{label}</dt>
            <dd className="min-w-0 break-words font-mono text-fg">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function FindingRow({ finding }: { finding: SkillImportFinding }) {
  const t = useT()
  const block = finding.severity === 'block'
  const known = FINDING_TEXT[finding.code]
  return (
    <li
      data-severity={finding.severity}
      className={cn(
        'flex items-start gap-2 rounded-control border px-2.5 py-1.5 text-sm',
        block ? 'border-danger/30 bg-danger/5 text-danger' : 'border-warn/30 bg-warn/10 text-warn',
      )}
    >
      {block ? <XCircle size={14} className="mt-0.5 shrink-0" aria-hidden /> : <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />}
      <span className="min-w-0">
        <span className="font-medium">{block ? t('막음') : t('주의')}</span>
        {' · '}
        {known ? t(known) : finding.message}
        {finding.file && finding.file.includes('/') ? <span className="font-mono text-faint"> · {finding.file}</span> : null}
        {finding.line ? <span className="text-faint"> · {t('{n}번째 줄').replace('{n}', String(finding.line))}</span> : null}
      </span>
    </li>
  )
}
