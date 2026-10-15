import { useEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')

/** Sun or moon for the brightness on screen; a click switches to the other one. */
export function ThemeToggle({ className }: { className?: string }) {
  const t = useT()
  const { theme, toggleTheme } = useStore()
  const [osDark, setOsDark] = useState(darkQuery.matches)

  useEffect(() => {
    const follow = () => setOsDark(darkQuery.matches)
    darkQuery.addEventListener('change', follow)
    return () => darkQuery.removeEventListener('change', follow)
  }, [])

  const dark = theme === 'system' ? osDark : theme === 'dark'
  const Icon = dark ? Moon : Sun

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      aria-label={t(dark ? '화면 밝기: 어둡게' : '화면 밝기: 밝게')}
      title={t(dark ? '밝게 바꾸기' : '어둡게 바꾸기')}
      className={className}
    >
      <Icon size={16} />
    </Button>
  )
}
