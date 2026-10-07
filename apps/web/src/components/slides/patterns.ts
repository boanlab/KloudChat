/**
 * Slide patterns beyond the base layouts. The mirror of
 * `apps/api/app/services/slide_patterns.wire()`: same order, same fields. A test keeps the
 * two equal, so keep this a plain literal and change both together.
 *
 * A pattern is a data shape (what the slide carries) drawn by a general arrangement
 * (`render`); `PatternView` implements the arrangements once.
 */

export type PatternShape = 'pairs' | 'columns' | 'metrics' | 'chart' | 'text'
export type PatternRender =
  | 'grid'
  | 'list'
  | 'flow'
  | 'vflow'
  | 'stack'
  | 'cycle'
  | 'quad'
  | 'columns'
  | 'kpi'
  | 'chart'
  | 'text'

export type PatternName =
  | 'cards-2'
  | 'cards-3'
  | 'feature-grid'
  | 'team'
  | 'checklist'
  | 'numbered'
  | 'faq'
  | 'glossary'
  | 'takeaways'
  | 'objectives'
  | 'references'
  | 'process'
  | 'chevron'
  | 'roadmap'
  | 'milestones'
  | 'pyramid'
  | 'funnel'
  | 'cycle'
  | 'swot'
  | 'matrix'
  | 'compare-2'
  | 'compare-3'
  | 'pros-cons'
  | 'before-after'
  | 'problem-solution'
  | 'myth-fact'
  | 'do-dont'
  | 'three-column'
  | 'kpi-grid'
  | 'stat-bars'
  | 'number-compare'
  | 'chart-pie'
  | 'chart-donut'
  | 'chart-hbar'
  | 'chart-stacked'
  | 'question'
  | 'definition'
  | 'hypothesis'

export interface Pattern {
  name: PatternName
  /** What the picker shows. */
  label: string
  shape: PatternShape
  render: PatternRender
  params: {
    cols?: number
    style?: string
    marker?: string
    shape?: string
    mode?: string
    tone?: string
    kind?: string
  }
  /** (fewest, most) items, or columns for `columns`. */
  count: [number, number]
}

export const PATTERNS: Pattern[] = [
  { name: 'cards-2', label: '카드 2개', shape: 'pairs', render: 'grid', params: { cols: 2, style: 'card' }, count: [2, 2] },
  { name: 'cards-3', label: '카드 3개', shape: 'pairs', render: 'grid', params: { cols: 3, style: 'card' }, count: [3, 3] },
  { name: 'feature-grid', label: '기능 격자', shape: 'pairs', render: 'grid', params: { cols: 3, style: 'feature' }, count: [4, 6] },
  { name: 'team', label: '팀·역할', shape: 'pairs', render: 'grid', params: { cols: 4, style: 'person' }, count: [2, 6] },
  { name: 'checklist', label: '체크리스트', shape: 'pairs', render: 'list', params: { marker: 'check' }, count: [3, 6] },
  { name: 'numbered', label: '번호 목록', shape: 'pairs', render: 'list', params: { marker: 'number' }, count: [3, 6] },
  { name: 'faq', label: '질문·답', shape: 'pairs', render: 'list', params: { marker: 'qa' }, count: [2, 4] },
  { name: 'glossary', label: '용어 정리', shape: 'pairs', render: 'list', params: { marker: 'term' }, count: [3, 6] },
  { name: 'takeaways', label: '핵심 요약', shape: 'pairs', render: 'list', params: { marker: 'star' }, count: [2, 4] },
  { name: 'objectives', label: '목표', shape: 'pairs', render: 'list', params: { marker: 'target' }, count: [2, 5] },
  { name: 'references', label: '참고문헌', shape: 'pairs', render: 'list', params: { marker: 'ref' }, count: [2, 6] },
  { name: 'process', label: '프로세스', shape: 'pairs', render: 'flow', params: { style: 'arrow' }, count: [3, 5] },
  { name: 'chevron', label: '쉐브론 단계', shape: 'pairs', render: 'flow', params: { style: 'chevron' }, count: [3, 5] },
  { name: 'roadmap', label: '로드맵', shape: 'pairs', render: 'flow', params: { style: 'phase' }, count: [3, 5] },
  { name: 'milestones', label: '마일스톤', shape: 'pairs', render: 'vflow', params: {}, count: [3, 6] },
  { name: 'pyramid', label: '피라미드', shape: 'pairs', render: 'stack', params: { shape: 'pyramid' }, count: [3, 5] },
  { name: 'funnel', label: '깔때기', shape: 'pairs', render: 'stack', params: { shape: 'funnel' }, count: [3, 5] },
  { name: 'cycle', label: '순환', shape: 'pairs', render: 'cycle', params: {}, count: [3, 6] },
  { name: 'swot', label: 'SWOT', shape: 'pairs', render: 'quad', params: { mode: 'swot' }, count: [4, 4] },
  { name: 'matrix', label: '2×2 매트릭스', shape: 'pairs', render: 'quad', params: { mode: 'matrix' }, count: [4, 4] },
  { name: 'compare-2', label: '두 안 비교', shape: 'columns', render: 'columns', params: { tone: 'vs' }, count: [2, 2] },
  { name: 'compare-3', label: '세 안 비교', shape: 'columns', render: 'columns', params: { tone: 'neutral' }, count: [3, 3] },
  { name: 'pros-cons', label: '장단점', shape: 'columns', render: 'columns', params: { tone: 'posneg' }, count: [2, 2] },
  { name: 'before-after', label: '전후 비교', shape: 'columns', render: 'columns', params: { tone: 'beforeafter' }, count: [2, 2] },
  { name: 'problem-solution', label: '문제·해결', shape: 'columns', render: 'columns', params: { tone: 'beforeafter' }, count: [2, 2] },
  { name: 'myth-fact', label: '오해와 사실', shape: 'columns', render: 'columns', params: { tone: 'posneg' }, count: [2, 2] },
  { name: 'do-dont', label: '할 것·하지 말 것', shape: 'columns', render: 'columns', params: { tone: 'posneg' }, count: [2, 2] },
  { name: 'three-column', label: '3단 구성', shape: 'columns', render: 'columns', params: { tone: 'neutral' }, count: [3, 3] },
  { name: 'kpi-grid', label: '지표 격자', shape: 'metrics', render: 'kpi', params: { mode: 'grid' }, count: [3, 6] },
  { name: 'stat-bars', label: '막대 지표', shape: 'metrics', render: 'kpi', params: { mode: 'bars' }, count: [2, 5] },
  { name: 'number-compare', label: '전후 수치', shape: 'metrics', render: 'kpi', params: { mode: 'compare' }, count: [2, 2] },
  { name: 'chart-pie', label: '원형 차트', shape: 'chart', render: 'chart', params: { kind: 'pie' }, count: [2, 6] },
  { name: 'chart-donut', label: '도넛 차트', shape: 'chart', render: 'chart', params: { kind: 'donut' }, count: [2, 6] },
  { name: 'chart-hbar', label: '가로 막대 차트', shape: 'chart', render: 'chart', params: { kind: 'hbar' }, count: [2, 8] },
  { name: 'chart-stacked', label: '누적 막대 차트', shape: 'chart', render: 'chart', params: { kind: 'stacked' }, count: [2, 6] },
  { name: 'question', label: '질문', shape: 'text', render: 'text', params: { mode: 'question' }, count: [1, 1] },
  { name: 'definition', label: '정의', shape: 'text', render: 'text', params: { mode: 'definition' }, count: [1, 1] },
  { name: 'hypothesis', label: '가설', shape: 'text', render: 'text', params: { mode: 'hypothesis' }, count: [1, 1] },
]

export const PATTERN_BY_NAME: Record<string, Pattern> = Object.fromEntries(PATTERNS.map((p) => [p.name, p]))

/** Whether a slide layout is one of the patterns. */
export function isPattern(layout: string | undefined | null): layout is PatternName {
  return Boolean(layout && Object.prototype.hasOwnProperty.call(PATTERN_BY_NAME, layout))
}

/** The pattern a layout names, or `null`. */
export function patternOf(layout: string | undefined | null): Pattern | null {
  return isPattern(layout) ? PATTERN_BY_NAME[layout] : null
}
