import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import './index.css'

/**
 * A deploy replaces the hashed chunks an open page was built against; the next lazy screen then
 * fails to load. Reload once to pick up the new build, instead of showing the failure. The mark
 * stops a loop when the reload does not help, and clears once the page has run a while.
 */
const RELOADED = 'kchat-chunk-reload'
window.addEventListener('vite:preloadError', (event) => {
  try {
    if (sessionStorage.getItem(RELOADED)) return
    sessionStorage.setItem(RELOADED, '1')
  } catch {
    return
  }
  event.preventDefault()
  window.location.reload()
})
window.setTimeout(() => {
  try {
    sessionStorage.removeItem(RELOADED)
  } catch {
    /* storage unavailable: nothing to clear */
  }
}, 15_000)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* Outermost so a throw anywhere shows a message instead of a blank page. */}
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
