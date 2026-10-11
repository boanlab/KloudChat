import type { DesignTemplateRow } from '@/lib/api'
import { DOC_FORMATS } from '@/components/report/docFormats'
import type { Agent } from '@/types'

/**
 * Detects chat sentences that are really orders for a document or a deck, so
 * the chat hands them to the report or slides surface. Deliberately narrow: a
 * document noun plus a writing verb, and not a question about documents or an
 * edit to an existing one.
 */

const NOUN =
  /(공문|공지문|안내문|보고서|기획서|제안서|계획서|회의록|보도자료|결과\s?보고|주간\s?보고|월간\s?보고|출장\s?보고|사후\s?분석|품의서|기안문|협조문|시말서|경위서|사유서|추천서|자기소개서|자소서|경력\s?기술서|백서|매뉴얼|지침서|규정|약관|계약서|의견서|건의문|성명서|설문지|term paper|white paper|proposal|report|memo|minutes)/i

const VERB =
  /(작성|써\s?줘|써\s?주|써줄|써\s?볼|만들어|만들\s?줘|초안|draft|write|compose|prepare|뽑아|작업해)/i

/** Questions *about* documents: stay in the chat wherever they appear. */
const ASKING =
  /(방법|요령|팁|어떻게|왜\s|무엇|뭐야|뭔가|뭐가|차이|예시|어떤\s?(식|것|게)|how to|what is|explain)/i

/** Edits to an existing document, judged on the sentence end where Korean puts its verb. */
const EDITING =
  /(검토|첨삭|고쳐|고치|수정|다듬|요약|번역|평가|피드백|분석|비교|읽어|봐\s?줘|보고\s?판단|review|summari[sz]e|translate|proofread)[^가-힣A-Za-z]{0,2}(해\s?줘|해\s?주|해줄|해\s?봐|해\s?주세요|해줄래|하자|해|줘|주세요|줄래)?\s*[.?!~]*$/i

/** Code that *produces* documents is a chat job, not a document: code is what is asked for
 *  (「스크립트를 짜 줘」, 「파이썬으로 자동 생성」), not a word in passing (「코드로 계산한 값」). */
const CODING =
  /((스크립트|코드|프로그램|함수|파이썬|python|javascript|typescript|sql|매크로|script|code)\s?(를|을|로|으로)?\s?(짜|작성|만들|구현|짜\s?줘|write|generate)|자동\s?생성|자동화|엑셀\s?매크로|\bapi\b)/i

/**
 * Message-sized writing: a text message, a chat or mail, a post or notice for a board, a few
 * lines. Answered in the chat even when the sentence calls it 안내문 or 공지문.
 */
const SHORT =
  /(답장|회신|답신|문자|메시지|카톡|카카오톡|알림톡|sms|이메일|메일|e-?mail|게시\s?글|게시물|공지\s?글|홈페이지\s?공지|팝업|배너|슬로건|캐치프레이즈|한\s?줄|두\s?줄|세\s?줄|\d{1,3}\s?(?:줄|자|글자|문장)(?:로|으로|\s?내외|\s?이내|\s?짜리)?(?![가-힣])|짧게|간단히|간단하게)/i

const DECK = /(슬라이드|발표\s?자료|발표\s?장표|장표|피피티|ppt|pptx|프레젠테이션|presentation|slide deck|slides)/i

function isOrder(s: string, noun: RegExp): boolean {
  return noun.test(s) && VERB.test(s) && !ASKING.test(s) && !EDITING.test(s) && !CODING.test(s)
}

/**
 * The person's own instruction, without what they pasted under it: the first paragraph, cut at
 * a blank line, a 「---」 or 「===」 rule, a quoted line or a pasted line over 400 characters
 * (the server's `context.instruction_part` reads the same way). A word inside a pasted mail
 * (「발표 자료를 11월 6일까지」) is not what the person asked for.
 */
export function instructionPart(text: string): string {
  const lines = text.trim().split('\n')
  const kept: string[] = []
  for (const [index, line] of lines.entries()) {
    if (index > 0 && (!line.trim() || /^\s{0,8}(?:-{3,}|={3,}|>)/.test(line) || line.length > 400)) break
    kept.push(line)
  }
  return kept.join('\n')
}

/** The surface a chat sentence really belongs to, or null to answer it here. */
export function handoffSurface(text: string): 'report' | 'slides' | null {
  const s = instructionPart(text).trim()
  if (s.length < 6 || s.length > 1200) return null
  if (SHORT.test(s)) return null
  if (isOrder(s, DECK)) return 'slides'
  if (isOrder(s, NOUN)) return 'report'
  return null
}

const SURFACE_WORDS = { report: /(보고서|문서|report)/i, slides: DECK } as const

/**
 * In an agent chat, the agent's document surface the sentence orders, or null to answer
 * here. Only surfaces the agent offers, and only by name: the bound document's own name
 * (its 서식 or format, e.g. 「경력기술서」) or the surface's word — so a 자기소개서 answer
 * that agent writes in chat is not taken for a document.
 */
export function agentHandoffSurface(
  text: string,
  offered: readonly string[],
  names: Partial<Record<'report' | 'slides', string[]>>,
): 'report' | 'slides' | null {
  const s = text.trim()
  if (s.length < 4 || s.length > 1200) return null
  for (const surface of ['slides', 'report'] as const) {
    if (!offered.includes(surface)) continue
    const own = (names[surface] ?? [])
      .map((name) => name.replace(/\s+/g, ''))
      .filter(Boolean)
      // 「경력 기술서」 is 「경력기술서」: at most one space between any two letters.
      .map((name) => [...name].map((c) => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s?'))
    const noun = own.length
      ? new RegExp(`(${own.join('|')})|${SURFACE_WORDS[surface].source}`, 'i')
      : SURFACE_WORDS[surface]
    if (isOrder(s, noun)) return surface
  }
  return null
}

/** The names an agent's bound documents go by, per surface: its 서식 and its format. */
export function agentDocumentNames(
  agent: Pick<Agent, 'renderTemplateId' | 'docFormat'>,
  templates: readonly Pick<DesignTemplateRow, 'id' | 'surface' | 'name' | 'nameEn'>[],
): Partial<Record<'report' | 'slides', string[]>> {
  const names: Partial<Record<'report' | 'slides', string[]>> = {}
  const template = templates.find((row) => row.id === agent.renderTemplateId)
  if (template && (template.surface === 'report' || template.surface === 'slides')) {
    names[template.surface] = [template.name, template.nameEn]
  }
  const format = DOC_FORMATS.find((row) => row.id === agent.docFormat)
  if (format) names.report = [...(names.report ?? []), format.label]
  return names
}
