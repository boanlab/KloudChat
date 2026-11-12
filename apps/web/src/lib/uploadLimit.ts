/** One file's upload ceiling, as `/auth/config` reports it; a larger file is refused before sending. */

import { currentLang, translate } from './i18n'

/** The API's default, until the config answers. */
let maxMb = 200

export function setMaxUploadMb(mb: number | undefined) {
  if (mb && mb > 0) maxMb = mb
}

export const overUploadLimit = (file: { size: number }) => file.size > maxMb * 1024 * 1024

/** The sentence for a file over the limit, sent or not; `mb` when the server named its own. */
export function tooLargeMessage(mb = maxMb): string {
  return translate(currentLang(), '파일이 너무 큽니다. 한 파일에 {n}MB까지 올릴 수 있습니다.').replace(
    '{n}',
    String(mb),
  )
}
