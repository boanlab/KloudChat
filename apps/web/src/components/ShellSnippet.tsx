import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Tabs } from '@/components/ui'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'

/** A command block with a copy button. */
export function Snippet({ text }: { text: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  return (
    <div className="group relative">
      <pre className="overflow-x-auto rounded-control border border-line bg-elevated px-3 py-2.5 text-sm leading-relaxed">
        <code className="font-mono">{text}</code>
      </pre>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t('복사')}
        title={t('명령을 클립보드로 복사합니다')}
        className="absolute top-1.5 right-1.5 opacity-60 transition-opacity hover:opacity-100 focus:opacity-100"
        onClick={async () => {
          if (!(await copyText(text))) return
          setCopied(true)
          setTimeout(() => setCopied(false), 1400)
        }}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </Button>
    </div>
  )
}

export type Shell = 'linux' | 'mac' | 'windows'

const STORAGE_KEY = 'kchat.shell'
const SHELLS: { id: Shell; label: string }[] = [
  { id: 'linux', label: 'Linux' },
  { id: 'mac', label: 'macOS' },
  { id: 'windows', label: 'Windows' },
]

/** The shell the visitor is most likely typing into, from the browser's platform. */
function detectShell(): Shell {
  const ua = typeof navigator === 'undefined' ? '' : navigator.userAgent
  if (/Windows/i.test(ua)) return 'windows'
  if (/Macintosh|Mac OS/i.test(ua)) return 'mac'
  return 'linux'
}

function initialShell(): Shell {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'linux' || saved === 'mac' || saved === 'windows') return saved
  } catch {
    /* storage may be unavailable */
  }
  return detectShell()
}

/**
 * Environment-variable assignments for each shell. Linux and macOS both take
 * `export`; Windows is written for PowerShell (`$env:NAME = "value"`).
 */
export function envCommands(vars: [name: string, value: string][]): Record<Shell, string> {
  const bash = vars.map(([k, v]) => `export ${k}=${v}`).join('\n')
  const powershell = vars.map(([k, v]) => `$env:${k} = "${v}"`).join('\n')
  return { linux: bash, mac: bash, windows: powershell }
}

/**
 * One snippet per shell behind Linux / macOS / Windows tabs. The tab opens on
 * the visitor's own platform and the last choice is remembered in this browser.
 */
export function ShellSnippet({
  commands,
  note,
}: {
  commands: Record<Shell, string>
  note?: Partial<Record<Shell, string>>
}) {
  const [shell, setShell] = useState<Shell>(initialShell)
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, shell)
    } catch {
      /* storage may be unavailable */
    }
  }, [shell])

  return (
    <div>
      <Tabs tabs={SHELLS} value={shell} onChange={setShell} />
      <div className="mt-2">
        <Snippet text={commands[shell]} />
      </div>
      {note?.[shell] && <p className="mt-1.5 text-sm text-faint">{note[shell]}</p>}
    </div>
  )
}
