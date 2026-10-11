/*
 * KloudChat bot on another site. Paste once, before </body>:
 *
 *   <script src="https://<kloudchat>/embed/widget.js" data-bot="<bot address>" async></script>
 *
 * Draws a button in the corner; it opens the bot in a frame (full screen on a phone). The frame
 * is the bot's embed page, which only the sites its administrator approved may show.
 * Optional: data-label="…" (the button's words), data-color="#5b53e8".
 */
(function () {
  'use strict'
  var script = document.currentScript
  if (!script) {
    var found = document.querySelectorAll('script[data-bot]')
    script = found[found.length - 1]
  }
  if (!script || script.getAttribute('data-kc-ready')) return
  script.setAttribute('data-kc-ready', '1')

  var slug = script.getAttribute('data-bot') || ''
  if (!/^[A-Za-z0-9-]{1,80}$/.test(slug)) return
  var origin = new URL(script.src, document.baseURI).origin
  var label = (script.getAttribute('data-label') || '챗봇에게 묻기').slice(0, 40)
  var color = script.getAttribute('data-color') || '#5b53e8'
  if (!/^#[0-9a-fA-F]{6}$/.test(color)) color = '#5b53e8'

  var Z = 2147483000
  var phone = window.matchMedia('(max-width: 640px)')
  var frame = null
  var open = false

  var button = document.createElement('button')
  button.type = 'button'
  button.setAttribute('aria-label', label)
  button.setAttribute('aria-expanded', 'false')
  button.title = label
  var bubble =
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
  var cross =
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>'
  button.innerHTML = bubble
  style(button, {
    position: 'fixed', right: '20px', bottom: '20px', width: '56px', height: '56px',
    borderRadius: '50%', border: '0', padding: '0', cursor: 'pointer', color: '#fff',
    background: color, boxShadow: '0 6px 20px rgba(0,0,0,.25)', zIndex: String(Z + 1),
    display: 'grid', placeItems: 'center',
  })

  function style(el, values) {
    for (var key in values) el.style[key] = values[key]
  }

  function place() {
    if (!frame) return
    if (phone.matches) {
      style(frame, { left: '0', top: '0', right: '0', bottom: '0', width: '100%', height: '100%', borderRadius: '0' })
      style(button, open ? { top: '10px', right: '10px', bottom: 'auto', width: '40px', height: '40px' }
        : { top: 'auto', right: '20px', bottom: '20px', width: '56px', height: '56px' })
    } else {
      style(frame, {
        left: 'auto', top: 'auto', right: '20px', bottom: '88px', width: '400px',
        height: 'min(640px, calc(100vh - 120px))', borderRadius: '16px',
      })
      style(button, { top: 'auto', right: '20px', bottom: '20px', width: '56px', height: '56px' })
    }
  }

  function toggle(next) {
    open = next
    if (open && !frame) {
      frame = document.createElement('iframe')
      frame.src = origin + '/embed/b/' + encodeURIComponent(slug)
      frame.title = label
      frame.setAttribute('allow', 'clipboard-write')
      style(frame, {
        position: 'fixed', border: '0', background: '#fff', zIndex: String(Z),
        boxShadow: '0 12px 40px rgba(0,0,0,.25)', colorScheme: 'normal',
      })
      document.body.appendChild(frame)
    }
    if (frame) frame.style.display = open ? 'block' : 'none'
    button.innerHTML = open ? cross : bubble
    button.setAttribute('aria-expanded', open ? 'true' : 'false')
    place()
  }

  button.addEventListener('click', function () { toggle(!open) })
  document.addEventListener('keydown', function (e) { if (open && e.key === 'Escape') toggle(false) })
  if (phone.addEventListener) phone.addEventListener('change', place)

  function mount() { document.body.appendChild(button) }
  if (document.body) mount()
  else document.addEventListener('DOMContentLoaded', mount)
})()
