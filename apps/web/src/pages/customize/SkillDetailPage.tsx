import { Copy, Download, FileCode2, Share2, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Markdown } from '@/components/chat/Markdown'
import { PageBody } from '@/components/layout/AppShell'
import { DetailLayout, DetailSection, HubTopBar, type MenuAction } from '@/components/customize/DetailLayout'
import { skillItem } from '@/components/customize/hubItems'
import { ShareDialog } from '@/components/customize/ShareDialog'
import { SkillForm } from '@/components/customize/SkillForm'
import { useItemActions } from '@/components/customize/tryItem'
import { Badge, ConfirmDialog, EmptyState, LoadingState, Switch } from '@/components/ui'
import { downloadSkill, errorMessage } from '@/lib/api'
import { useT, useTIn } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** /customize/skills/:id — an installed skill or a store one: what it does and its steps. */
export function SkillDetailPage() {
  const t = useT()
  const tHub = useTIn('hub')
  const navigate = useNavigate()
  const { skillId = '' } = useParams()
  const skills = useStore((s) => s.skills)
  const skillStore = useStore((s) => s.skillStore)
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const loadSkillStore = useStore((s) => s.loadSkillStore)
  const workspaceLoading = useStore((s) => s.workspaceLoading)
  const skillStoreLoading = useStore((s) => s.skillStoreLoading)
  const toggleSkill = useStore((s) => s.toggleSkill)
  const upsertSkill = useStore((s) => s.upsertSkill)
  const deleteSkill = useStore((s) => s.deleteSkill)
  const setNotice = useStore((s) => s.setNotice)
  const { tryItem, addItem } = useItemActions()
  const [sharing, setSharing] = useState(false)
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    void loadWorkspace()
    void loadSkillStore()
  }, [loadWorkspace, loadSkillStore])

  const own = skills.find((s) => s.id === skillId)
  const store = own ? undefined : skillStore.find((s) => s.id === skillId)
  const skill = own ?? store
  if (!skill) {
    return (
      <>
        <HubTopBar tab="skills" tabLabel="스킬" title="" />
        <PageBody>
          {workspaceLoading || skillStoreLoading || skills.length === 0 ? (
            <LoadingState label={t('스킬을 불러오는 중…')} />
          ) : (
            <EmptyState icon={<Sparkles size={18} />} title={t('이 스킬을 찾을 수 없습니다')} description={t('지워졌거나 볼 수 없는 스킬입니다.')} />
          )}
        </PageBody>
      </>
    )
  }

  const item = skillItem(skill, !own)
  const editable = !!own && own.source !== 'built-in'

  const menu: MenuAction[] = []
  if (editable) menu.push({ label: '공유', icon: <Share2 size={14} />, run: () => setSharing(true) })
  if (own) {
    menu.push({
      label: '복제',
      icon: <Copy size={14} />,
      run: async () => {
        try {
          const copy = await upsertSkill({
            ...own,
            id: '',
            slug: '',
            source: 'personal',
            catalogKey: null,
            originId: null,
            name: `${t(own.name)} ${t('사본')}`.slice(0, 80),
            visibility: 'private',
          })
          navigate(`/customize/skills/${copy.id}`)
        } catch (err) {
          setNotice(errorMessage(err, t('복제하지 못했습니다.')))
        }
      },
    })
  }
  if (own) {
    menu.push({
      label: '내보내기 (SKILL.md)',
      icon: <Download size={14} />,
      run: async () => {
        try {
          await downloadSkill(own.id, own.name)
        } catch (err) {
          setNotice(errorMessage(err, t('내보내지 못했습니다.')))
        }
      },
    })
  }
  if (editable) menu.push({ label: '삭제', icon: <Trash2 size={14} />, run: () => setConfirming(true), danger: true })

  return (
    <>
      <DetailLayout
        item={item}
        tab="skills"
        tabLabel="스킬"
        onTry={() => tryItem(item)}
        primary={
          own
            ? editable
              ? { label: '편집', run: () => document.getElementById('settings')?.scrollIntoView({ behavior: 'smooth' }) }
              : undefined
            : {
                label: store?.installed ? '추가됨' : '추가',
                added: store?.installed,
                run: async () => {
                  const to = await addItem(item)
                  if (to) navigate(to)
                },
              }
        }
        menu={menu}
        aside={
          own && (
            <Switch
              checked={own.enabled}
              onChange={() => void toggleSkill(own.id)}
              label={t('{name} 설치 상태').replace('{name}', t(own.name))}
            />
          )
        }
      >
        <DetailSection title={tHub('개요')}>
          {/* The description is the header's line; it is not said twice. */}
          {skill.whenToUse && skill.whenToUse !== skill.description && (
            <div className="rounded-card border border-line bg-panel px-4 py-3">
              <p className="mb-1 text-sm font-medium text-faint">{t('사용 시점')}</p>
              <p className="text-base text-muted">{t(skill.whenToUse)}</p>
            </div>
          )}
          <p className="flex flex-wrap gap-1.5">
            <Badge>{t('약 {n} 토큰').replace('{n}', skill.estimatedTokens.toLocaleString())}</Badge>
            <Badge>v{skill.version}</Badge>
            {skill.scripts?.map((script) => (
              <Badge key={script} title={t('정해진 스크립트를 샌드박스에서 첨부 파일에 실행합니다.')}>
                <span className="font-mono">scripts/{script}.py</span>
              </Badge>
            ))}
          </p>
        </DetailSection>

        <DetailSection title={t('절차')}>
          {skill.body.trim() ? (
            <div className="max-h-[32rem] overflow-y-auto rounded-card border border-line bg-panel px-4 py-3">
              <p className="mb-2 flex items-center gap-1.5 text-sm text-faint">
                <FileCode2 size={13} aria-hidden />
                <span className="font-mono">SKILL.md</span>
              </p>
              <Markdown>{skill.body}</Markdown>
            </div>
          ) : (
            <p className="text-base text-faint">{t('절차가 비어 있습니다.')}</p>
          )}
        </DetailSection>

        {editable && own && (
          <DetailSection id="settings" title={t('설정')}>
            <SkillForm key={`${own.id}:${own.visibility}:${own.enabled}`} current={own} onSaved={() => undefined} aboveBottomBar />
          </DetailSection>
        )}
      </DetailLayout>

      {sharing && own && <ShareDialog open onClose={() => setSharing(false)} skill={own} />}
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => {
          if (own) void deleteSkill(own.id)
          navigate('/customize?tab=skills')
        }}
        title={t('{name} 삭제').replace('{name}', t(skill.name))}
        description={t('되돌릴 수 없습니다. 이 스킬을 쓰던 대화는 그대로 남습니다.')}
      />
    </>
  )
}
