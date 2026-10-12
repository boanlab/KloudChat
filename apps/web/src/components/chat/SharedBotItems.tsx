import { useNavigate } from 'react-router-dom'
import { MenuItem, MenuLabel } from '@/components/ui'
import { useMemberBots } from '@/lib/publishing'
import { useT } from '@/lib/useT'

/** 「+」 › 에이전트: agents other members published, after the org-shared ones; each opens on its own page. */
export function SharedBotItems({ withLabel, onPick }: { withLabel: boolean; onPick: () => void }) {
  const t = useT()
  const navigate = useNavigate()
  const bots = useMemberBots()
  if (bots.length === 0) return null
  return (
    <>
      {withLabel && <MenuLabel>{t('공유된 에이전트')}</MenuLabel>}
      {bots.map((bot) => (
        <MenuItem
          key={bot.slug}
          label={t('{name} — 만든 사람: {owner}').replace('{name}', bot.title).replace('{owner}', bot.ownerName)}
          hint={<span data-testid="shared-agent-owner">{bot.ownerName}</span>}
          onClick={() => {
            onPick()
            navigate(`/b/${bot.slug}`)
          }}
        >
          {bot.title}
        </MenuItem>
      ))}
    </>
  )
}
