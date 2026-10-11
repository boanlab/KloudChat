import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import './index.css'

/** After a deploy, a missing lazy chunk reloads once; the mark prevents loops and clears after a while. */
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
