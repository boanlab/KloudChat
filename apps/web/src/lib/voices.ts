/** Narration voices the gateway accepts; anything else falls back to `alloy`. */
export const VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'] as const

/** The tone of each OpenAI-style voice in a few words: tone only, nothing about the speaker. */
export const VOICE_TONE: Record<string, string> = {
  alloy: '중립적이고 균형 잡힌 톤',
  ash: '차분하고 담백한 톤',
  ballad: '부드럽고 잔잔한 톤',
  coral: '따뜻하고 친근한 톤',
  echo: '차분하고 또렷한 톤',
  fable: '이야기를 들려주듯 표정이 풍부한 톤',
  nova: '밝고 활기찬 톤',
  onyx: '낮고 묵직한 톤',
  sage: '침착하고 안정된 톤',
  shimmer: '부드럽고 맑은 톤',
  verse: '또렷하고 표현이 풍부한 톤',
}
