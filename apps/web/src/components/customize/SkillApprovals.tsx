import { FileCode } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Card, EmptyState, Field, Modal, Textarea } from '@/components/ui'
import { adminSkillsApi, errorMessage, type SkillReview } from '@/lib/api'
import { useT } from '@/lib/useT'
import { ScriptRow } from './SkillServerFields'

/**
 * 「스킬 검토」: skills with scripts asking to be shared. A skill is not a 진행 과정, so it has a
 * queue of its own beside 「진행 과정 검토」, with the same approve / reject-with-reason steps;
 * the administrator reads each script here before anyone else can take a copy.
 */
export function SkillApprovals() {
  const t = useT()
  const [rows, setRows] = useState<SkillReview[] | null>(null)
  const [rejecting, setRejecting] = useState<SkillReview | null>(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const reload = useCallback(
    () =>
      adminSkillsApi
        .list()
        .then((v) => setRows(Array.isArray(v) ? v : []))
        .catch(() => setRows([])),
    [],
  )
  useEffect(() => {
    void reload()
  }, [reload])
  return (
    <section className="space-y-2 pt-8" aria-label={t('스킬 검토')} data-testid="skill-approvals">
      <h2 className="text-lg font-semibold">{t('스킬 검토')}</h2>
      <p className="text-sm text-muted">
        {t('직접 올린 스크립트가 있는 스킬은 승인한 뒤에야 다른 사람이 복사해 씁니다. 스크립트는 코드 실행 샌드박스에서만 돕니다.')}
      </p>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {rows !== null && rows.length === 0 ? (
        <EmptyState icon={<FileCode size={18} />} title={t('검토를 기다리는 스킬이 없습니다')} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {(rows ?? []).map(({ skill, ownerName }) => (
            <Card key={skill.id} className="space-y-2 p-4" data-testid="skill-approval">
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 text-base font-semibold">{skill.name}</p>
                <Badge>{t('스크립트 {n}개').replace('{n}', String(skill.scriptFiles?.length ?? 0))}</Badge>
              </div>
              <p className="text-sm text-faint">{ownerName}</p>
              {skill.description && <p className="text-sm text-muted">{skill.description}</p>}
              <ul className="space-y-1.5">
                {(skill.scriptFiles ?? []).map((script) => (
                  <ScriptRow key={script.name} script={script} />
                ))}
              </ul>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="primary"
                  onClick={async () => {
                    try {
                      await adminSkillsApi.approve(skill.id)
                      void reload()
                    } catch (err) {
                      setError(errorMessage(err, t('승인하지 못했습니다.')))
                    }
                  }}
                >
                  {t('승인')}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  onClick={() => {
                    setNote('')
                    setRejecting({ skill, ownerName })
                  }}
                >
                  {t('반려')}
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      <Modal
        open={!!rejecting}
        onClose={() => setRejecting(null)}
        title={t('반려 사유')}
        footer={
          <>
            <Button type="button" onClick={() => setRejecting(null)}>
              {t('취소')}
            </Button>
            <Button
              type="button"
              variant="primary"
              disabled={!note.trim()}
              onClick={async () => {
                if (!rejecting) return
                try {
                  await adminSkillsApi.reject(rejecting.skill.id, note)
                  setRejecting(null)
                  void reload()
                } catch (err) {
                  setError(errorMessage(err, t('반려하지 못했습니다.')))
                }
              }}
            >
              {t('반려')}
            </Button>
          </>
        }
      >
        <Field label={t('반려 사유')} hint={t('만든 사람에게 그대로 보입니다.')}>
          <Textarea rows={3} maxLength={400} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </Modal>
    </section>
  )
}
