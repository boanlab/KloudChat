/**
 * Whether a sentence typed into a chat is really an order for a document.
 *
 * "협조 공문 작성해줘" answered in a chat bubble is the wrong shape: it comes
 * back as prose with no sections, no file, no 서식, and the person then
 * copies it out by hand. The report surface exists for exactly this, so the
 * chat hands the sentence over instead of answering it.
 *
 * Deliberately narrow. A document *noun* alone is not enough — "보고서 요약
 * 해줘" and "기획서 쓰는 요령 알려줘" are chat questions about a document —
 * so the sentence must also carry a writing verb, and must not be asking how,
 * why, or for a fix to something already written.
 */

const NOUN =
  /(공문|공지문|안내문|보고서|기획서|제안서|계획서|회의록|보도자료|결과\s?보고|주간\s?보고|월간\s?보고|출장\s?보고|사후\s?분석|품의서|기안문|협조문|시말서|경위서|사유서|추천서|자기소개서|자소서|백서|매뉴얼|지침서|규정|약관|계약서|의견서|건의문|성명서|설문지|term paper|white paper|proposal|report|memo|minutes)/i

const VERB =
  /(작성|써\s?줘|써\s?주|써줄|써\s?볼|만들어|만들\s?줘|초안|draft|write|compose|prepare|뽑아|작업해)/i

/** Questions *about* documents: stay in the chat wherever they appear. */
const ASKING =
  /(방법|요령|팁|어떻게|왜\s|무엇|뭐야|뭔가|뭐가|차이|예시|어떤\s?(식|것|게)|how to|what is|explain)/i

/**
 * Edits to a document that already exists: judged on the end of the
 * sentence, where Korean puts its verb. "녹취록 요약해서 회의록 작성해줘" is an
 * order for a document even though 요약 is in it; "이 보고서 요약해 줘" is not.
 */
const EDITING =
  /(검토|첨삭|고쳐|고치|수정|다듬|요약|번역|평가|피드백|분석|비교|읽어|봐\s?줘|보고\s?판단|review|summari[sz]e|translate|proofread)[^가-힣A-Za-z]{0,2}(해\s?줘|해\s?주|해줄|해\s?봐|해\s?주세요|해줄래|하자|해|줘|주세요|줄래)?\s*[.?!~]*$/i

/** Code that *produces* documents is a chat job, not a document. */
const CODING =
  /(스크립트|코드|프로그램|함수|자동\s?생성|자동화|파이썬|python|javascript|typescript|api|sql|엑셀\s?매크로|매크로|script|code)/i

const DECK = /(슬라이드|발표\s?자료|발표\s?장표|장표|피피티|ppt|pptx|프레젠테이션|presentation|slide deck|slides)/i

function isOrder(s: string, noun: RegExp): boolean {
  return noun.test(s) && VERB.test(s) && !ASKING.test(s) && !EDITING.test(s) && !CODING.test(s)
}

/** The surface a chat sentence really belongs to, or null to answer it here. */
export function handoffSurface(text: string): 'report' | 'slides' | null {
  const s = text.trim()
  if (s.length < 6 || s.length > 1200) return null
  if (isOrder(s, DECK)) return 'slides'
  if (isOrder(s, NOUN)) return 'report'
  return null
}

export function wantsDocument(text: string): boolean {
  return handoffSurface(text) === 'report'
}
