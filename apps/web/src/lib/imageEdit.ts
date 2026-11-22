import { BASE_URL } from '@/lib/api'
import type { ModelInfo, RoutingMode, SessionKind } from '@/types'

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
  /** The model that drew the picture, when known (a strict-local one keeps it inside). */
  madeBy?: string | null
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

/** Picture models this account may use that change a given picture, edit-only ones (on no
 *  surface: they draw nothing new) included. */
export function editingModels(models: ModelInfo[]): ModelInfo[] {
  return models.filter((m) => m.modality === 'image' && m.editsImages === true)
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

/** Mirrors the server's `_edit_image` rules: strict-local or internal-material pictures need a strict-local editing model, otherwise the picked model must edit. */
export function editBlock({
  session,
  modelId,
  models,
  internal,
  internalRule,
  madeBy,
  t,
}: {
  session:
    | {
        kind: SessionKind
        model?: string | null
        routingMode?: RoutingMode
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
  /** The model that drew the picture; a strict-local model's picture never goes to an outside editor. */
  madeBy?: string | null
  t: T
}): EditBlock {
  const editors = editingModels(models)
  const current = models.find((m) => m.id === session?.model)
  const inside = (m: ModelInfo) => m.strictLocal
  // kept inside, the server picks the strict-local editor itself, whatever picture model is picked
  const insideEditor = editors.some(inside)
  const strictSession = !!current?.strictLocal || session?.routingMode === 'super_strict'
  const strictTurn = strictSession && session?.kind !== 'image'
  if (madeBy && models.find((m) => m.id === madeBy)?.strictLocal) {
    return insideEditor ? null : { reason: t('strict-local 모델이 그린 그림이라 외부 모델로 고칠 수 없습니다.'), switchTo: null }
  }
  if (strictSession && insideEditor) return null
  if (strictTurn) return { reason: t('strict-local 대화라 외부 모델로 고칠 수 없습니다.'), switchTo: null }
  const internalTurn =
    internalRule &&
    (internal || !!session?.projectId || (session?.messages ?? []).some((m) => (m.attachments?.length ?? 0) > 0))
  if (internalTurn) {
    return insideEditor ? null : { reason: t('내부 자료라 외부 모델로 고칠 수 없습니다.'), switchTo: null }
  }
  const model = models.find((m) => m.id === modelId)
  if (model?.editsImages) return null
  // a model to switch the picture pick to draws too: an edit-only one would leave nothing to draw with
  const drawing = editors.filter((m) => m.kinds.includes('image'))
  const usable = drawing.filter((m) => !m.strictLocal).concat(drawing.filter(inside))
  if (!usable.length) return { reason: t('고칠 수 있는 이미지 모델이 없습니다.'), switchTo: null }
  return {
    reason: model
      ? t('이 모델({model})은 그리기만 합니다.').replace('{model}', shortName(model))
      : t('이미지 모델을 고르세요.'),
    switchTo: usable[0],
  }
}

/** The model's name for a sentence: no vendor, and no trailing parenthesis (it nests in the sentence's own). */
function shortName(model: ModelInfo): string {
  const name = (model.name || model.label).replace(/\s*\([^)]*\)\s*$/, '').trim()
  return name || model.name || model.label
}
