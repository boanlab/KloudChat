import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { reportUncaughtErrors } from './lib/clientErrors'
import { leaveOffice, officeDirty } from './lib/officeGuard'
import { BUILD_VERSION } from './lib/version'
import './index.css'

/** After a deploy, a missing lazy chunk reloads once per bundle: the mark is keyed by this build, so a
 * second deploy inside the window still recovers, while one broken build cannot loop. */
const RELOADED = 'kchat-chunk-reload'
const reloadedHere = `${RELOADED}:${BUILD_VERSION}`
/** The reload waits on the unsaved-edits question: one at a time. */
let asking = false
try {
  // Marks left by earlier builds' pages, which reloaded before they could clear them.
  for (const key of Object.keys(sessionStorage)) {
    if (key.startsWith(RELOADED) && key !== reloadedHere) sessionStorage.removeItem(key)
  }
} catch {
  /* storage unavailable: nothing to clear */
}
window.addEventListener('vite:preloadError', (event) => {
  try {
    if (sessionStorage.getItem(reloadedHere)) return
  } catch {
    return
  }
  event.preventDefault()
  // an office document's unsaved edits are asked about first; the reload waits for the answer
  const reload = () => {
    try {
      sessionStorage.setItem(reloadedHere, '1')
    } catch {
      return
    }
    window.location.reload()
  }
  if (!officeDirty()) return reload()
  if (asking) return
  asking = true
  void leaveOffice('reload').then((go) => {
    asking = false
    if (go) reload()
  })
})
window.setTimeout(() => {
  try {
    sessionStorage.removeItem(reloadedHere)
  } catch {
    /* storage unavailable: nothing to clear */
  }
}, 15_000)
reportUncaughtErrors()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* Outermost so a throw anywhere shows a message instead of a blank page. */}
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
