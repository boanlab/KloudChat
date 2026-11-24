/** The speech model that reads text aloud: self-hosted only, so no text leaves the deployment. */
import { useStore } from '@/store/useStore'
import type { ModelInfo } from '@/types'
import { VOICE_KEY, voicesOf } from './voices'

/** The default audio model if it speaks, else the first self-hosted one that does. */
export function pickSpeechModel(models: ModelInfo[], defaultAudio?: string): ModelInfo | undefined {
  const own = (m: ModelInfo) => m.speechEndpoint && m.strictLocal && m.dataBoundary === 'self_hosted'
  return models.find((m) => m.id === defaultAudio && own(m)) ?? models.find((m) => own(m) && m.kinds.includes('av'))
}

export function useSpeechModel(): ModelInfo | undefined {
  const models = useStore((s) => s.models)
  const defaultAudio = useStore((s) => s.avModelByMode.audio)
  return pickSpeechModel(models, defaultAudio)
}

/** The remembered voice if the model offers it, else the model's first. */
export function voiceFor(model?: ModelInfo): string {
  const offered = voicesOf(model)
  let stored = ''
  try {
    stored = localStorage.getItem(VOICE_KEY) ?? ''
  } catch {
    // no storage: the model's own first voice
  }
  return offered.includes(stored) ? stored : (offered[0] ?? 'sohee')
}
