import { Monitor, Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

const icons = { system: Monitor, light: Sun, dark: Moon }

const labels = {
  system: '테마: 시스템 설정 따름',
  light: '테마: 밝게',
  dark: '테마: 어둡게',
}

/** Cycles system, light, dark; the icon shows the current state. */
export function ThemeToggle({ className }: { className?: string }) {
  const t = useT()
  const { theme, toggleTheme } = useStore()
  const Icon = icons[theme]

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      aria-label={t(labels[theme])}
      title={t('시스템·밝게·어둡게 순으로 바뀝니다')}
      className={className}
    >
      <Icon size={16} />
    </Button>
  )
}
