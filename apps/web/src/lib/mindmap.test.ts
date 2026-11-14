import { describe, expect, it } from 'vitest'
import { isMindmap, parseMindmap, pictureSource } from './mindmap'

const big = [
  'mindmap',
  '  root((클라우드 컴퓨팅))',
  ...Array.from({ length: 6 }, (_, b) => [
    `    갈래 ${b + 1}`,
    ...Array.from({ length: 5 }, (_, c) => [
      `      항목 ${b + 1}-${c + 1}`,
      ...Array.from({ length: 3 }, (_, g) => `        세부 ${b + 1}-${c + 1}-${g + 1}`),
    ]).flat(),
  ]).flat(),
].join('\n')

describe('mind map', () => {
  it('reads the tree by indentation, shapes and ids dropped', () => {
    const root = parseMindmap('mindmap\n  root((분산 시스템))\n    id1[합의]\n      래프트\n      팍소스\n    (시계)\n      ::icon(fa fa-clock)\n      "벡터 시계"')
    expect(root).toEqual({
      label: '분산 시스템',
      children: [
        { label: '합의', children: [{ label: '래프트', children: [] }, { label: '팍소스', children: [] }] },
        { label: '시계', children: [{ label: '벡터 시계', children: [] }] },
      ],
    })
  })

  it('knows a mind map from other diagrams', () => {
    expect(isMindmap('%% note\nmindmap\n  root((a))')).toBe(true)
    expect(isMindmap('flowchart LR\n  a --> b')).toBe(false)
    expect(parseMindmap('flowchart LR\n  a --> b')).toBeNull()
  })

  it('draws a large mind map two levels deep, a small one whole', () => {
    const picture = pictureSource(big)
    const tree = parseMindmap(picture)!
    expect(tree.children).toHaveLength(6)
    expect(tree.children[0].children).toHaveLength(5)
    expect(tree.children[0].children[0].children).toHaveLength(0)
    expect(picture).not.toContain('세부')
    const small = 'mindmap\n  root((a))\n    b\n      c'
    expect(pictureSource(small)).toBe(small)
    expect(pictureSource('flowchart LR\n  a --> b')).toBe('flowchart LR\n  a --> b')
  })
})
