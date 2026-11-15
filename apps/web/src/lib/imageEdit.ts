import { BASE_URL } from '@/lib/api'
import type { ModelInfo, SessionKind } from '@/types'

/** The picture 「이미지 고치기」 starts from; the composer of `sessionId` carries it. */
export interface ImageEditSource {
  sessionId: string
  /** The image artifact, when the picture is one made here; the server finds it from a file too. */
  artifactId?: string | null
  fileId?: string | null
  /** `/api/files/<id>/content` */
  src: string
  name: string
  /** `upload`: a picture the person gave (internal material); `generated`: made here. */
  origin: 'upload' | 'generated'
  /** Made from a picture the person gave. */
  internal?: boolean
}

/** Why the picture cannot be changed here, and the model that could change it when that helps. */
export type EditBlock = { reason: string; switchTo: ModelInfo | null } | null

type T = (text: string) => string

const PICTURE = /\.(png|jpe?g|webp|gif|bmp)$/i

/** Whether a file is a picture 「이미지 고치기」 can start from. */
export function isPicture(name: string, mime?: string | null): boolean {
  // a drawing (SVG) is no raster picture a model takes
  if (mime?.includes('svg') || /\.svg$/i.test(name)) return false
  return !!mime?.startsWith('image/') || PICTURE.test(name)
}

export function fileSrc(fileId: string): string {
  return `${BASE_URL}/files/${fileId}/content`
}

/** Picture models this account may use that change a given picture. */
export function editingModels(models: ModelInfo[]): ModelInfo[] {
  return models.filter((m) => m.kinds.includes('image') && m.editsImages === true)
}

/** The picture model an edit in this conversation goes to: an image conversation's own, else
 *  the person's image pick. */
export function imageModelId(
  session: { kind: SessionKind; model?: string | null } | undefined,
  modelByKind: Partial<Record<SessionKind, string>>,
): string {
  if (session?.kind === 'image' && session.model) return session.model
  return modelByKind.image ?? ''
}

/**
 * The server's rules (`_edit_image`), read ahead so the action can say why it is off: a
 * strict-local conversation and internal material (under the internal-material rule) keep the
 * picture inside, where no model edits; otherwise the picked model has to edit.
 */
export function editBlock({
  session,
  modelId,
  models,
  internal,
  internalRule,
  t,
}: {
  session:
    | {
        kind: SessionKind
        model?: string | null
        projectId?: string | null
        messages?: { attachments?: unknown[] }[]
      }
    | undefined
  modelId: string
  models: ModelInfo[]
  /** The picture itself is internal material (an upload, or made from one). */
  internal: boolean
  /** The organisation keeps internal material on strict-local models. */
  internalRule: boolean
  t: T
}): EditBlock {
  const editors = editingModels(models)
  const current = models.find((m) => m.id === session?.model)
  const strictTurn = !!current?.strictLocal && session?.kind !== 'image'
  const inside = (m: ModelInfo) => m.strictLocal
  if (strictTurn) {
    return editors.some(inside) ? null : { reason: t('strict-local 대화라 외부 모델로 고칠 수 없습니다.'), switchTo: null }
  }
  const internalTurn =
    internalRule &&
    (internal || !!session?.projectId || (session?.messages ?? []).some((m) => (m.attachments?.length ?? 0) > 0))
  if (internalTurn && !editors.some(inside)) {
    return { reason: t('내부 자료라 외부 모델로 고칠 수 없습니다.'), switchTo: null }
  }
  const model = models.find((m) => m.id === modelId)
  if (model?.editsImages) return null
  const usable = internalTurn ? editors.filter(inside) : editors.filter((m) => !m.strictLocal).concat(editors.filter(inside))
  if (!usable.length) return { reason: t('고칠 수 있는 이미지 모델이 없습니다.'), switchTo: null }
  return {
    reason: model
      ? t('이 모델({model})은 그리기만 합니다.').replace('{model}', model.name || model.label)
      : t('이미지 모델을 고르세요.'),
    switchTo: usable[0],
  }
}
