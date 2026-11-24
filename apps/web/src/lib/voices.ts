/** Narration voices of the OpenAI-style chat-audio model; the default when a model lists none. */
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

/** Qwen3-TTS presets, as the model card words them; sohee is the Korean one and our default. */
export const QWEN_VOICES = [
  'sohee', 'vivian', 'serena', 'uncle_fu', 'dylan', 'eric', 'ryan', 'aiden', 'ono_anna',
] as const

/** Tone only, from the model card's one-line descriptions of the nine presets. */
Object.assign(VOICE_TONE, {
  sohee: '따뜻하고 감정이 풍부한 톤',
  vivian: '밝고 약간 날카로운 톤',
  serena: '따뜻하고 부드러운 톤',
  uncle_fu: '낮고 부드러운 톤',
  dylan: '맑고 자연스러운 톤',
  eric: '활기차고 살짝 쉰 듯한 밝은 톤',
  ryan: '역동적이고 리듬감이 강한 톤',
  aiden: '밝고 중음이 또렷한 톤',
  ono_anna: '장난기 있고 가볍고 날렵한 톤',
})

/** The voices a model offers: its own list, else the OpenAI-style six. */
export function voicesOf(model?: { voices?: string[] } | null): readonly string[] {
  return model?.voices?.length ? model.voices : VOICES
}

/** The voice that is sent: the pick if the model has it, else the model's first (Qwen3-TTS: sohee). */
export function voiceFor(model: { voices?: string[] } | null | undefined, picked: string): string {
  const voices = voicesOf(model)
  return voices.includes(picked) ? picked : voices[0]
}
