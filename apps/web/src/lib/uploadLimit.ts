/** One file's upload ceiling, as `/auth/config` reports it; a larger file is refused before sending. */

import { currentLang, translate } from './i18n'

/** The API's default, until the config answers. */
let maxMb = 200

export function setMaxUploadMb(mb: number | undefined) {
  if (mb && mb > 0) maxMb = mb
}

export const overUploadLimit = (file: { size: number }) => file.size > maxMb * 1024 * 1024

/** Audio/video go up in pieces to their own, larger limit; `reading`: the server reads them. */
let mediaMb = 500
let reading = false

export function setMediaUpload(config: { maxMediaMb?: number; mediaReading?: boolean } | undefined) {
  if (config?.maxMediaMb && config.maxMediaMb > 0) mediaMb = config.maxMediaMb
  reading = Boolean(config?.mediaReading)
}

export const maxMediaMb = () => mediaMb
export const mediaReading = () => reading
export const overMediaLimit = (file: { size: number }) => file.size > mediaMb * 1024 * 1024

/** The sentence for a file over the limit, sent or not; `mb` when the server named its own. */
export function tooLargeMessage(mb = maxMb): string {
  return translate(currentLang(), '파일이 너무 큽니다. 한 파일에 {n}MB까지 올릴 수 있습니다.').replace(
    '{n}',
    String(mb),
  )
}

/** Whether a file goes up in pieces as media: audio/video by type or name. */
export const isMediaFile = (file: { type: string; name: string }) =>
  /^(audio|video)\//.test(file.type) || /\.(mp4|m4v|mov|webm|mkv|avi|mp3|m4a|wav|ogg|oga|flac|aac|opus|mpe?g)$/i.test(file.name)

/** Whether this file is over the limit that applies to it, and that limit's sentence. */
export function overLimit(file: { size: number; type: string; name: string }): string | null {
  if (isMediaFile(file) && mediaReading()) return overMediaLimit(file) ? tooLargeMessage(mediaMb) : null
  return overUploadLimit(file) ? tooLargeMessage() : null
}
