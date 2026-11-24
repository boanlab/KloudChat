/** Text for a Korean voice: symbols, parentheses, numbers and Latin words turned into what is said aloud.
 * Only a text that is mostly Hangul gets the Korean readings; an English answer is left to an English voice. */

const EMOJI = /[\p{Extended_Pictographic}‍︎️]/gu
const HANGUL = /[가-힣]/g
const LETTER = /\p{L}/gu

/** Hangul is a real share of the letters, as the server judges the language of a request. */
export function koreanLike(text: string): boolean {
  const letters = text.match(LETTER)?.length ?? 0
  return letters > 0 && (text.match(HANGUL)?.length ?? 0) / letters >= 0.3
}

const DIGIT = ['영', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구']
const DIGIT_PLAIN = ['공', '일', '이', '삼', '사', '오', '육', '칠', '팔', '구']

/** 2026 → 이천이십육. Sino-Korean; above 16 digits it is read digit by digit. */
export function sino(n: number | string): string {
  const text = String(n)
  if (!/^\d+$/.test(text)) return text
  if (text.length > 16) return [...text].map((d) => DIGIT[+d]).join('')
  let value = Number(text)
  if (value === 0) return '영'
  const four = (g: number) => {
    let out = ''
    for (const [place, name] of [[1000, '천'], [100, '백'], [10, '십']] as const) {
      const d = Math.floor(g / place) % 10
      if (d) out += (d === 1 ? '' : DIGIT[d]) + name
    }
    const one = g % 10
    return out + (one ? DIGIT[one] : '')
  }
  const names = ['', '만', '억', '조']
  const groups: string[] = []
  for (let i = 0; value > 0; i += 1) {
    const g = value % 10000
    value = Math.floor(value / 10000)
    if (g) groups.unshift(i === 1 && g === 1 ? '만' : four(g) + names[i])
  }
  return groups.join(' ')
}

const TENS = ['', '열', '스물', '서른', '마흔', '쉰', '예순', '일흔', '여든', '아흔']
const ONES = ['', '한', '두', '세', '네', '다섯', '여섯', '일곱', '여덟', '아홉']

/** 3 → 세, 14 → 열네, 20 → 스무: the number in front of a counter. Above 99 it is Sino-Korean. */
export function native(n: number): string {
  if (n < 1 || n > 99) return sino(n)
  if (n === 20) return '스무'
  return TENS[Math.floor(n / 10)] + ONES[n % 10]
}

const COUNTERS = '개|명|시|살|마리|권|장|대|잔|곳|가지'

const UNITS: Record<string, string> = {
  km: '킬로미터', kg: '킬로그램', cm: '센티미터', mm: '밀리미터', ml: '밀리리터', gb: '기가바이트',
  mb: '메가바이트', tb: '테라바이트', kb: '킬로바이트', ghz: '기가헤르츠', mhz: '메가헤르츠',
  ms: '밀리초',
}
const UNIT_RE = new RegExp(
  `(\\d(?:[\\d,]*\\d)?(?:\\.\\d+)?)\\s*(${Object.keys(UNITS).join('|')})(?![A-Za-z])`,
  'gi',
)
const SHORT_UNITS: Record<string, string> = { m: '미터', g: '그램', l: '리터', s: '초' }

/** Digits in a Korean text as they are read: 3.5 → 삼 점 오, 1,000 → 천, 2026년 → 이천이십육년, 3개 → 세 개. */
export function readNumbers(text: string): string {
  return (
    text
      .replace(/(?<![\w.])(\d{1,3}(?:,\d{3})+)(?![\d])/g, (m) => m.replace(/,/g, ''))
      // 14:30 → 14시 30분
      .replace(/(?<!\d)(\d{1,2}):([0-5]\d)(?![\d:])/g, (m, h: string, min: string) =>
        +h <= 24 ? `${+h}시${min === '00' ? '' : ` ${+min}분`}` : m,
      )
      .replace(/(\d)\s*[~～–]\s*(\d)/g, '$1에서 $2')
      .replace(/(\d)\s*[°º]\s*C\b|(\d)\s*℃/g, (_m, a?: string, b?: string) => `${a ?? b} 도`)
      .replace(UNIT_RE, (_m, n: string, u: string) => `${n} ${UNITS[u.toLowerCase()]}`)
      .replace(/(\d)\s*([mgls])(?![A-Za-z])/g, (_m, n: string, u: string) => `${n} ${SHORT_UNITS[u]}`)
      // phone-like and zero-led numbers are said digit by digit
      .replace(/(?<![\d.])0\d{1,2}-\d{3,4}-\d{4}(?!\d)/g, (m) =>
        [...m.replace(/-/g, ' ')].map((c) => (c === ' ' ? ' ' : DIGIT_PLAIN[+c])).join(''),
      )
      .replace(/(?<![\d.])0\d+(?![\d.])/g, (m) => [...m].map((c) => DIGIT_PLAIN[+c]).join(''))
      .replace(/(?<=[A-Za-z가-힣])-(?=\d)/g, ' ')
      .replace(/(?<![\w.])([1-9]|1\d)\s?월/g, (_m, n: string) =>
        `${+n === 6 ? '유' : +n === 10 ? '시' : sino(n)}월`,
      )
      .replace(new RegExp(`(?<![\\w.])(\\d{1,2})\\s?(${COUNTERS})(?!월)`, 'g'), (_m, n: string, c: string) =>
        `${native(+n)} ${c}`,
      )
      .replace(new RegExp(`(?<![\\w.])(\\d{3,})\\s?(${COUNTERS})(?!월)`, 'g'), (_m, n: string, c: string) =>
        `${sino(n)} ${c}`,
      )
      .replace(/(?<![\w.])(\d+)\.(\d+)(?![\w.])/g, (_m, a: string, b: string) =>
        `${sino(a)} 점 ${[...b].map((d) => DIGIT[+d]).join(' ')}`,
      )
      .replace(/(?<![\w.])\d+(?![\w])/g, (m) => sino(m))
  )
}

/** Acronyms: the letters as Koreans say them. */
const ACRONYMS: Record<string, string> = {
  AI: '에이아이', API: '에이피아이', CPU: '씨피유', GPU: '지피유', RAM: '램', SSD: '에스에스디',
  HDD: '에이치디디', URL: '유알엘', PDF: '피디에프', SQL: '에스큐엘', IT: '아이티', OS: '오에스',
  PC: '피씨', UI: '유아이', UX: '유엑스', DB: '디비', HTTP: '에이치티티피', HTTPS: '에이치티티피에스',
  HTML: '에이치티엠엘', CSS: '씨에스에스', JSON: '제이슨', XML: '엑스엠엘', CSV: '씨에스브이',
  VPN: '브이피엔', DNS: '디엔에스', IP: '아이피', TCP: '티씨피', LLM: '엘엘엠', GPT: '지피티',
  TTS: '티티에스', STT: '에스티티', OCR: '오씨알', CEO: '씨이오', KPI: '케이피아이', ROI: '알오아이',
  SaaS: '사스', PaaS: '파스', IaaS: '아이에이에이에스', AWS: '에이더블유에스', GCP: '지씨피',
  USB: '유에스비', OK: '오케이', FAQ: '에프에이큐', SNS: '에스엔에스', QR: '큐알', SDK: '에스디케이',
  RAG: '랙', MCP: '엠씨피', PPT: '피피티', PPTX: '피피티엑스', DOCX: '독스', XLSX: '엑셀',
  'R&D': '알앤디', 'Wi-Fi': '와이파이',
}
/** Words that are said in Hangul; matched without regard to case. */
const WORDS: Record<string, string> = {
  cloud: '클라우드', computing: '컴퓨팅', server: '서버', data: '데이터', database: '데이터베이스',
  network: '네트워크', software: '소프트웨어', hardware: '하드웨어', internet: '인터넷',
  online: '온라인', offline: '오프라인', app: '앱', web: '웹', email: '이메일', file: '파일',
  service: '서비스', system: '시스템', storage: '스토리지', security: '시큐리티', code: '코드',
  excel: '엑셀', word: '워드', powerpoint: '파워포인트', google: '구글', microsoft: '마이크로소프트',
  amazon: '아마존', python: '파이썬', java: '자바', javascript: '자바스크립트',
  typescript: '타입스크립트', linux: '리눅스', windows: '윈도우', docker: '도커',
  kubernetes: '쿠버네티스', github: '깃허브', git: '깃', chatgpt: '챗지피티', claude: '클로드',
  openai: '오픈에이아이', super: '슈퍼', agent: '에이전트', model: '모델', chat: '챗',
}
const LETTER_NAME: Record<string, string> = {
  A: '에이', B: '비', C: '씨', D: '디', E: '이', F: '에프', G: '지', H: '에이치', I: '아이',
  J: '제이', K: '케이', L: '엘', M: '엠', N: '엔', O: '오', P: '피', Q: '큐', R: '알', S: '에스',
  T: '티', U: '유', V: '브이', W: '더블유', X: '엑스', Y: '와이', Z: '제트',
}
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\&-]/g, '\\$&')
const ACRONYM_RE = new RegExp(
  `(?<![A-Za-z0-9])(${Object.keys(ACRONYMS).sort((a, b) => b.length - a.length).map(escape).join('|')})s?(?![A-Za-z0-9])`,
  'g',
)
const WORD_RE = new RegExp(
  `(?<![A-Za-z0-9])(${Object.keys(WORDS).sort((a, b) => b.length - a.length).join('|')})(?:s|es)?(?![A-Za-z0-9])`,
  'gi',
)
const CAPS_RE = /(?<![A-Za-z0-9])[A-Z]{2,5}(?![A-Za-z0-9])/g

/** English acronyms and common loanwords written as they are said. */
export function readLatin(text: string): string {
  return text
    .replace(ACRONYM_RE, (_m, k: string) => ACRONYMS[k])
    .replace(WORD_RE, (_m, k: string) => WORDS[k.toLowerCase()])
    .replace(CAPS_RE, (m) => [...m].map((c) => LETTER_NAME[c]).join(''))
}

const ASIDE = /[(（]([^()（）]*)[)）]/g

/** A short Hangul aside is read as part of the sentence; a long, Latin-only or empty one is dropped. */
export function readAsides(text: string): string {
  let current = text
  for (let i = 0; i < 4; i += 1) {
    const next = current.replace(ASIDE, (_m, inner: string) => {
      const body = inner.trim()
      if (!body) return ''
      if (body.length <= 24 && /[가-힣]/.test(body)) return `, ${body}, `
      if (body.length <= 12 && /\d/.test(body) && !/[A-Za-z]{3,}/.test(body)) return `, ${body}, `
      return ' '
    })
    if (next === current) break
    current = next
  }
  return current.replace(/[()（）]/g, ' ')
}

/** Everything a Korean voice trips on, made sayable. `text` is already free of markdown. */
export function forSpeech(text: string): string {
  const clean = readAsides(text.replace(EMOJI, ''))
    .replace(/(\d)\s*[~～–]\s*(\d)/g, '$1에서 $2')
    .replace(/["“”„‟«»]/g, '')
    .replace(/\s*(?:→|⇒|->|=>|—|–|…)\s*/g, ', ')
    .replace(/\s*[·•‧]\s*/g, ', ')
    .replace(/(?<=[가-힣])\s*\/\s*(?=[가-힣])/g, ' 또는 ')
    .replace(/(?<=[A-Za-z가-힣])\s*\/\s*(?=[A-Za-z가-힣])/g, ' ')
    .replace(/\s*\/\s*/g, ' ')
    .replace(/\s*~\s*(?!\d)/g, ', ')
    .replace(/\s*[|]\s*/g, ', ')
    .replace(/\s*&\s*/g, ' 그리고 ')
  const spoken = koreanLike(clean)
    ? readLatin(readNumbers(clean.replace(/\s*%/g, ' 퍼센트')))
    : clean
  return spoken
    .replace(/\s+([,.!?])/g, '$1')
    .replace(/,(\s*,)+/g, ',')
    .replace(/^[\s,]+/, '')
    .replace(/,\s*([.!?])/g, '$1')
    .replace(/,\s*$/, '')
    .replace(/\s+/g, ' ')
    .trim()
}
