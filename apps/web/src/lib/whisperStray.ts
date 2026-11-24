/** Whisper makes words out of room noise; for a short clip they are almost always one of a few phrases. */

const STRAY = [
  '감사합니다',
  '시청해주셔서감사합니다',
  '시청해주셔서고맙습니다',
  '구독과좋아요',
  '구독과좋아요부탁드립니다',
  '다음영상에서만나요',
  '자막제공',
  'mbc뉴스',
  'thankyou',
  'thanksforwatching',
  'thankyouforwatching',
  'bye',
  'you',
]

/** Clips under this long are the ones a stray phrase can come from. */
const SHORT_SECONDS = 3

/** Seconds in a 16 kHz mono WAV from its size (44-byte header, 2 bytes a sample). */
export const wavSeconds = (bytes: number) => Math.max(0, bytes - 44) / 32_000

/** A stock phrase from a short clip is noise, not something the person said. */
export function isStray(text: string, seconds: number): boolean {
  if (seconds >= SHORT_SECONDS) return false
  const plain = text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '')
  return STRAY.includes(plain)
}
