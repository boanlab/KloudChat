/**
 * Fetches the HWP editor's files (rhwp-studio's page, its script and its ~27 MB WebAssembly)
 * into the browser's cache ahead of the editor, so the first 한글 document opens without
 * waiting on the download after the conversation and the document are made. Once per page;
 * nothing is run, and a failure only means the editor fetches them itself, as before.
 */

/** The studio's path, as `STUDIO_URL` in HwpPanel (not imported: that module carries the SDK). */
const STUDIO = '/rhwp/'

let warming: Promise<void> | null = null

async function fetchText(url: string): Promise<string> {
  const response = await fetch(url, { credentials: 'same-origin' })
  return response.ok ? response.text() : ''
}

/** Reads a response to its end, so the cache holds all of it. */
async function fetchAll(url: string): Promise<void> {
  const response = await fetch(url, { credentials: 'same-origin' })
  if (response.ok) await response.arrayBuffer()
}

/** The same-origin `/rhwp/…` paths `pattern` finds in `text`. */
function studioPaths(text: string, pattern: RegExp): string[] {
  const found = new Set<string>()
  for (const match of text.matchAll(pattern)) {
    const path = match[1]
    if (path.startsWith(STUDIO)) found.add(path)
  }
  return [...found]
}

export function warmStudio(): Promise<void> {
  warming ??= (async () => {
    const page = await fetchText(STUDIO)
    // the page's module scripts (the studio's bundle), as its own <script> tags name them
    const scripts = studioPaths(page, /<script[^>]*type="module"[^>]*src="([^"]+)"/g)
    await Promise.all(
      scripts.map(async (script) => {
        const code = await fetchText(script)
        // the WebAssembly the bundle loads (wasm-bindgen's `new URL(…, import.meta.url)`)
        const wasm = studioPaths(code, /["'`](\/rhwp\/assets\/[\w.-]+\.wasm)["'`]/g)
        await Promise.all(wasm.map(fetchAll))
      }),
    )
  })().catch(() => {
    // the editor fetches them itself; a later warm may try again
    warming = null
  })
  return warming
}
