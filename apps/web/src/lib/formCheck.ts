/**
 * The one way a form refuses to send: the submit button stays enabled, and pressing it with a
 * required field empty puts focus on that field and says what is missing, under the field.
 * A disabled button gives no reason and drops out of the tab order.
 */

import { translate, type Lang } from './i18n'

/** The password rule until `/auth/config` answers, and against a server that does not say. */
export const DEFAULT_PASSWORD_MIN = 10

/** Whether the last syllable of `word` closes on a consonant; null for a word not in Hangul. */
function closes(word: string): boolean | null {
  const code = word.trim().charCodeAt(word.trim().length - 1) - 0xac00
  if (Number.isNaN(code) || code < 0 || code > 11171) return null
  return code % 28 !== 0
}

/** `을` or `를` after `word`. */
export function objectParticle(word: string): string {
  const shut = closes(word)
  return shut === null ? '을(를)' : shut ? '을' : '를'
}

/** `은` or `는` after `word`. */
export function topicParticle(word: string): string {
  const shut = closes(word)
  return shut === null ? '은(는)' : shut ? '은' : '는'
}

/** 「{label}을(를) 입력하세요.」 with the right particle, in the screen's language. */
export function missingMessage(lang: Lang, label: string): string {
  if (lang === 'en') {
    return translate(lang, '{field}{을} 입력하세요.').replace('{field}', label.toLowerCase()).replace('{을}', '')
  }
  return `${label}${objectParticle(label)} 입력하세요.`
}

export interface RequiredField<K extends string = string> {
  key: K
  /** The field's label as shown. */
  label: string
  value: string
  /** The control to focus when this is the first one empty. */
  control: { current: HTMLElement | null }
}

/**
 * The first required field left blank, focused, with its message; null when all are filled.
 */
export function checkRequired<K extends string>(
  lang: Lang,
  fields: RequiredField<K>[],
): { key: K; message: string } | null {
  const missing = fields.find((field) => !field.value.trim())
  if (!missing) return null
  missing.control.current?.focus()
  return { key: missing.key, message: missingMessage(lang, missing.label) }
}
