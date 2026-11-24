/** App install state: Chromium's deferred prompt, Safari's manual steps, installed or not. */
import { useEffect, useState } from 'react'
import { isIOS } from './push'
import { useMediaQuery } from './useMediaQuery'

/** Chrome's deferred install prompt; not in lib.dom. */
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

// Fires once, early, usually before mount.
let deferred: BeforeInstallPromptEvent | null = null
const listeners = new Set<() => void>()
if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()
    deferred = e as BeforeInstallPromptEvent
    listeners.forEach((fn) => fn())
  })
  window.addEventListener('appinstalled', () => {
    deferred = null
    listeners.forEach((fn) => fn())
  })
}

/** Desktop Safari: File → Add to Dock, no prompt. */
const isMacSafari = () =>
  navigator.platform === 'MacIntel' &&
  navigator.maxTouchPoints <= 1 &&
  /Safari\//.test(navigator.userAgent) &&
  !/Chrome|Chromium|CriOS|Edg|OPR|Firefox/.test(navigator.userAgent)

/** Chromium offers no prompt once the app is installed (the address bar shows 「앱에서 열기」 instead). */
const isChromium = () => /Chrome|Chromium|Edg\//.test(navigator.userAgent) && !/Firefox|OPR/.test(navigator.userAgent)

/** Install state shared by the top bar button and 설정 › 앱. */
export function useInstall() {
  const standaloneQuery = useMediaQuery('(display-mode: standalone)')
  const [, bump] = useState(0)
  const [howTo, setHowTo] = useState(false)
  useEffect(() => {
    const fn = () => bump((n) => n + 1)
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }, [])
  const standalone =
    standaloneQuery ||
    ('standalone' in navigator && Boolean((navigator as Navigator & { standalone?: boolean }).standalone))
  const ios = isIOS()
  const macSafari = !ios && isMacSafari()
  const install = async () => {
    if (!deferred) {
      setHowTo(true)
      return
    }
    const prompt = deferred
    await prompt.prompt()
    const { outcome } = await prompt.userChoice
    if (outcome === 'accepted') deferred = null
    bump((n) => n + 1)
  }
  return {
    standalone,
    /** Chromium offered its install prompt. */
    canPrompt: deferred !== null,
    ios,
    macSafari,
    /** Chromium with no prompt to offer: installed already, or its own menu has the install item. */
    chromium: !ios && !macSafari && isChromium(),
    /** Something to press: a prompt, or Safari's steps. */
    installable: !standalone && (deferred !== null || ios || macSafari),
    install,
    howTo,
    closeHowTo: () => setHowTo(false),
  }
}

