import { describe, expect, it } from 'vitest'
import type { ModelInfo } from '@/types'
import { editBlock } from './imageEdit'

const model = (id: string, extra: Partial<ModelInfo>) =>
  ({ id, label: id, name: id, kinds: ['image'], modality: 'image', editsImages: false, strictLocal: false, ...extra }) as ModelInfo
const inside = model('strict-local/z-image', { name: 'Z Image Turbo (Local)', strictLocal: true })
const editor = model('ext/edit', { editsImages: true })
const t = (text: string) => text

describe('editBlock', () => {
  const base = { session: undefined, modelId: editor.id, models: [inside, editor], internal: false, internalRule: false, t }

  it('lets an outside editor change an outside picture', () => {
    expect(editBlock(base)).toBeNull()
  })

  it('keeps a picture a strict-local model drew away from an outside editor', () => {
    expect(editBlock({ ...base, madeBy: inside.id })?.reason).toContain('strict-local 모델이 그린 그림')
  })

  it('names a drawing-only model without a nested parenthesis', () => {
    const block = editBlock({ ...base, modelId: inside.id })
    expect(block?.reason).toBe('이 모델(Z Image Turbo)은 그리기만 합니다.')
  })
})

describe('editBlock with a strict-local model that only edits', () => {
  // on no surface: it draws nothing new, so no picker lists it
  const insideEditor = model('strict-local/qwen-image-edit', {
    kinds: [],
    editsImages: true,
    drawsImages: false,
    strictLocal: true,
  })
  const drawer = model('ext/draw', {})
  const chatInside = model('strict-local/chat', { kinds: ['chat'], modality: 'chat', strictLocal: true })
  const chatOutside = model('ext/chat', { kinds: ['chat'], modality: 'chat' })
  const models = [inside, drawer, insideEditor, chatInside, chatOutside]
  const base = { modelId: drawer.id, models, internal: false, internalRule: false, t }

  it('offers the edit in a strict-local conversation', () => {
    expect(editBlock({ ...base, session: { kind: 'chat', model: chatInside.id } })).toBeNull()
  })

  it('offers the edit in a Super Agent strict conversation', () => {
    expect(
      editBlock({ ...base, session: { kind: 'chat', model: chatOutside.id, routingMode: 'super_strict' } }),
    ).toBeNull()
  })

  it('offers the edit in an image conversation on a strict-local model', () => {
    expect(editBlock({ ...base, modelId: inside.id, session: { kind: 'image', model: inside.id } })).toBeNull()
  })

  it('offers the edit of a picture a strict-local model drew', () => {
    expect(editBlock({ ...base, session: { kind: 'chat', model: chatOutside.id }, madeBy: inside.id })).toBeNull()
  })

  it('offers the edit of internal material kept inside', () => {
    const session = { kind: 'chat' as const, model: chatOutside.id }
    expect(editBlock({ ...base, session, internal: true, internalRule: true })).toBeNull()
  })

  it('still says why a strict-local conversation cannot edit when no strict-local model edits', () => {
    const block = editBlock({ ...base, models: [inside, drawer, chatInside], session: { kind: 'chat', model: chatInside.id } })
    expect(block?.reason).toBe('strict-local 대화라 외부 모델로 고칠 수 없습니다.')
  })

  it('never offers the edit-only model as the picture pick', () => {
    const block = editBlock({ ...base, session: { kind: 'chat', model: chatOutside.id } })
    expect(block?.switchTo).toBeNull()
    expect(block?.reason).toBe('고칠 수 있는 이미지 모델이 없습니다.')
  })
})
