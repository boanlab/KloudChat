import { describe, expect, it } from 'vitest'
import type { ModelInfo } from '@/types'
import { editBlock } from './imageEdit'

const model = (id: string, extra: Partial<ModelInfo>) =>
  ({ id, label: id, name: id, kinds: ['image'], editsImages: false, strictLocal: false, ...extra }) as ModelInfo
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
