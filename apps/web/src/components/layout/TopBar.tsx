import { Languages, PanelLeft } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import { InstallButton } from './InstallButton'
import { ThemeToggle } from './ThemeToggle'

export function TopBar({ left, right }: { left?: ReactNode; right?: ReactNode }) {
  const { cycleSidebar, lang, toggleLang } = useStore()
  const t = useT()

  return (
    /* No `backdrop-filter`: it would become the containing block for the
       fixed-position dialogs opened from this bar. */
    <header className="flex h-13 min-w-0 shrink-0 items-center gap-2 border-b border-line bg-bg px-3 max-sm:px-2">
      <Button
        variant="ghost"
        size="icon"
        onClick={cycleSidebar}
        aria-label={t('사이드바 토글')}
        title={t('사이드바를 접거나 폅니다')}
      >
        <PanelLeft size={16} />
      </Button>
      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">{left}</div>
      {/* On phones the buttons collapse to icons; aria-label/title keep the full name. */}
      <div className="flex shrink-0 items-center gap-1.5 max-sm:gap-0.5 max-sm:[&>button]:w-8 max-sm:[&>button]:overflow-hidden max-sm:[&>button]:px-0 max-sm:[&>button]:text-[0px] max-sm:[&>span]:hidden">
        {right}
        <InstallButton />
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleLang}
          /* aria-label carries the visible text too, since it replaces it. */
          aria-label={`${t('언어 전환')} · ${lang === 'ko' ? 'EN' : '한'}`}
          title={t('언어 전환')}
        >
          <Languages size={16} />
          <span className="text-sm font-medium">{lang === 'ko' ? 'EN' : '한'}</span>
        </Button>
        <ThemeToggle />
      </div>
    </header>
  )
}
