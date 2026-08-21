import { Languages, PanelLeft } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import { AccountMenuCompact } from './AccountMenu'
import { ThemeToggle } from './ThemeToggle'

export function TopBar({ left, right }: { left?: ReactNode; right?: ReactNode }) {
  const { toggleSidebar, lang, toggleLang } = useStore()
  const t = useT()

  return (
    /* Opaque, not frosted. `backdrop-filter` makes this bar the containing
       block for every `position: fixed` descendant, and 공유 opens its dialog
       from in here — so the frost pinned a full-screen modal inside a 52px
       strip, where the conversation painted straight over it and the buttons
       could not be pressed. Nothing ever scrolls under this bar for the blur
       to blur, so the bar simply stops being see-through. */
    <header className="flex h-13 shrink-0 items-center gap-2 border-b border-line bg-bg px-3">
      <Button
        variant="ghost"
        size="icon"
        onClick={toggleSidebar}
        aria-label={t('사이드바 토글')}
        title={t('사이드바를 접거나 폅니다')}
      >
        <PanelLeft size={16} />
      </Button>
      <div className="flex min-w-0 flex-1 items-center gap-2">{left}</div>
      <div className="flex items-center gap-1.5">
        {right}
        {/* 아이콘만으로는 무엇으로 바뀌는지 알 수 없어 바뀔 언어를 함께 적는다 */}
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleLang}
          aria-label={t('언어 전환')}
          title={t('언어 전환')}
        >
          <Languages size={16} />
          <span className="text-sm font-medium">{lang === 'ko' ? 'EN' : '한'}</span>
        </Button>
        <ThemeToggle />
        {/* 계정 — 로그아웃까지 포함해 모든 화면에서 두 번의 조작 안에 있다.
            사이드바 접힘 상태나 좁은 화면에서는 이쪽이 유일한 경로다. */}
        <AccountMenuCompact />
      </div>
    </header>
  )
}
