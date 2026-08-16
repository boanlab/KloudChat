import { Languages, Moon, PanelLeft, Sun } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

export function TopBar({ left, right }: { left?: ReactNode; right?: ReactNode }) {
  const { toggleSidebar, theme, toggleTheme, lang, toggleLang } = useStore()
  const t = useT()

  return (
    <header className="flex h-13 shrink-0 items-center gap-2 border-b border-line bg-bg/85 px-3 backdrop-blur">
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
          <span className="text-[12px] font-medium">{lang === 'ko' ? 'EN' : '한'}</span>
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleTheme}
          aria-label={t('테마 전환')}
          title={t('밝은 화면과 어두운 화면을 바꿉니다')}
        >
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </Button>
      </div>
    </header>
  )
}
