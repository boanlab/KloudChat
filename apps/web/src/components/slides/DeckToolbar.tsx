import {
  BarChart3, Baseline, Bold, Eraser, Image as ImageIcon, Italic, LayoutTemplate, Minus,
  MoreVertical, Palette, Play, Plus, Redo2, ShieldQuestion, Table2, Underline, Undo2,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Btn, Menu, Sep } from '@/components/pptx/chrome'
import { useT } from '@/lib/useT'

/** The text colours, as the PowerPoint editor's toolbar offers them. */
export const TEXT_COLORS = [
  '#000000', '#434343', '#666666', '#999999', '#cccccc', '#ffffff', '#c00000', '#e69138',
  '#f1c232', '#6aa84f', '#45818e', '#3c78d8', '#3d85c6', '#674ea7', '#a64d79', '#e06666',
  '#ffd966', '#93c47d', '#6fa8dc', '#8e7cc3',
]
/** Size steps, % of the text's own size (a selection) or of the slide's (no selection). */
export const SIZE_STEPS = [80, 90, 100, 110, 120, 140, 160, 200]
const ZOOMS = [50, 75, 100, 150, 200]

export interface DeckToolbarProps {
  width: number
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
  /** `fit`, or the slide's width as a share of 960 px. */
  zoom: 'fit' | number
  /** What 맞춤 shows now, %. */
  fitPercent: number
  onZoom: (zoom: 'fit' | number) => void
  /** The 레이아웃 menu's items. */
  layouts: (close: () => void) => ReactNode
  /** The 이미지 menu's items. */
  image: (close: () => void) => ReactNode
  tablePressed: boolean
  onTable: () => void
  chartPressed: boolean
  onChart: () => void
  /** The deck's typeface (고딕, 명조) and the choices. */
  font: string
  fonts: [string, string][]
  onFont: (font: string) => void
  /** Text is selected (or the caret is) in a box on the slide. */
  textActive: boolean
  /** Size shown: the selection's, else the slide's, %. */
  size: number
  onSize: (percent: number) => void
  bold: boolean
  italic: boolean
  underline: boolean
  color: string
  onFormat: (command: 'bold' | 'italic' | 'underline' | 'foreColor' | 'removeFormat', value?: string) => void
  /** The 디자인 menu's items: theme, accent colours, this slide's look, several slides at once. */
  design: (close: () => void) => ReactNode
  /** Findings to look at (검토). */
  reviewCount: number
  reviewOpen: boolean
  onReview: () => void
  onPresent: () => void
}

/**
 * The web slides' one toolbar row, in the PowerPoint editor's order and with its icons:
 * 실행 취소 · 다시 실행 · 확대/축소 | 레이아웃 · 이미지 · 표 · 차트 | 글꼴 · 크기 | 굵게 · 기울임꼴 ·
 * 밑줄 · 텍스트 색상 · 서식 지우기 | 디자인, then 검토 and 발표 at the end. A web slide places its
 * content by layout, so the layout, the table and the chart stand where the PowerPoint editor
 * inserts a text box, a shape or a table; its paragraphs follow the layout, so no paragraph
 * group. Narrow, the type controls fold into ⋮ as the PowerPoint editor folds its own.
 */
export function DeckToolbar(p: DeckToolbarProps) {
  const t = useT()
  const narrow = p.width < 780
  const zoomLabel = p.zoom === 'fit' ? `${p.fitPercent}%` : `${Math.round(p.zoom * 100)}%`
  const step = (by: -1 | 1) => {
    const at = SIZE_STEPS.findIndex((size) => size >= p.size)
    const from = at < 0 ? SIZE_STEPS.length - 1 : SIZE_STEPS[at] === p.size ? at : by > 0 ? at - 1 : at
    p.onSize(SIZE_STEPS[Math.max(0, Math.min(SIZE_STEPS.length - 1, from + by))])
  }

  const fontMenu = (closeOuter?: () => void) => (
    <Menu label={t('글꼴')} width={160} testid="font"
      button={<span className="kc-font">{t(p.fonts.find(([id]) => id === p.font)?.[1] ?? p.font)}</span>}>
      {(close) => p.fonts.map(([id, label]) => (
        <button key={id} type="button" className={'kc-item' + (id === p.font ? ' on' : '')}
          onClick={() => { p.onFont(id); close(); closeOuter?.() }}>
          {t(label)}
        </button>
      ))}
    </Menu>
  )
  const sizeControls = (
    <>
      <Btn label={t('글꼴 크기 줄이기')} icon={Minus} disabled={p.size <= SIZE_STEPS[0]} onClick={() => step(-1)} />
      <Menu label={t('글꼴 크기')} width={80} testid="size"
        button={<span className="kc-size">{p.size}%</span>}>
        {(close) => SIZE_STEPS.map((size) => (
          <button key={size} type="button" className={'kc-item' + (size === p.size ? ' on' : '')}
            onClick={() => { p.onSize(size); close() }}>
            {size}%
          </button>
        ))}
      </Menu>
      <Btn label={t('글꼴 크기 늘리기')} icon={Plus} disabled={p.size >= SIZE_STEPS[SIZE_STEPS.length - 1]}
        onClick={() => step(1)} />
    </>
  )

  return (
    <div className="kc-toolbar" role="toolbar" aria-label={t('슬라이드 도구')}>
      <Btn label={t('실행 취소')} icon={Undo2} disabled={!p.canUndo} onClick={p.onUndo} />
      <Btn label={t('다시 실행')} icon={Redo2} disabled={!p.canRedo} onClick={p.onRedo} />
      <Menu label={t('확대/축소')} testid="zoom" button={<span className="kc-zoom">{zoomLabel}</span>}>
        {(close) => (
          <>
            <button type="button" className={'kc-item' + (p.zoom === 'fit' ? ' on' : '')}
              onClick={() => { p.onZoom('fit'); close() }}>
              {t('맞춤')}
            </button>
            {ZOOMS.map((zoom) => (
              <button key={zoom} type="button" className={'kc-item' + (p.zoom === zoom / 100 ? ' on' : '')}
                onClick={() => { p.onZoom(zoom / 100); close() }}>
                {zoom}%
              </button>
            ))}
          </>
        )}
      </Menu>
      <Sep />
      <Menu label={t('레이아웃')} width={224} testid="layout" button={<LayoutTemplate size={16} />}>
        {p.layouts}
      </Menu>
      <Menu label={t('이미지')} width={208} testid="image" button={<ImageIcon size={16} />}>
        {p.image}
      </Menu>
      <Btn label={t('표')} icon={Table2} pressed={p.tablePressed} onClick={p.onTable} />
      <Btn label={t('차트')} icon={BarChart3} pressed={p.chartPressed} onClick={p.onChart} />
      <Sep />
      {narrow ? (
        <Menu label={t('글꼴과 크기')} testid="type" button={<span className="kc-size">{p.size}%</span>}>
          {(close) => (
            <div className="kc-row">
              {fontMenu(close)}
              {sizeControls}
            </div>
          )}
        </Menu>
      ) : (
        <>
          {fontMenu()}
          {sizeControls}
        </>
      )}
      <Sep />
      <Btn label={t('굵게')} icon={Bold} disabled={!p.textActive} pressed={p.textActive && p.bold}
        onClick={() => p.onFormat('bold')} />
      <Btn label={t('기울임꼴')} icon={Italic} disabled={!p.textActive} pressed={p.textActive && p.italic}
        onClick={() => p.onFormat('italic')} />
      <Btn label={t('밑줄')} icon={Underline} disabled={!p.textActive} pressed={p.textActive && p.underline}
        onClick={() => p.onFormat('underline')} />
      <Menu label={t('텍스트 색상')} disabled={!p.textActive} width={232} testid="color"
        button={(
          <span className="kc-color">
            <Baseline size={16} />
            <i style={{ background: p.color }} />
          </span>
        )}>
        {(close) => (
          <div className="kc-swatches">
            {TEXT_COLORS.map((hex) => (
              <button key={hex} type="button" className="kc-sw" title={hex} aria-label={hex}
                style={{ background: hex }}
                onClick={() => { p.onFormat('foreColor', hex); close() }} />
            ))}
          </div>
        )}
      </Menu>
      {narrow ? (
        <Menu label={t('더보기')} testid="more" button={<MoreVertical size={16} />}>
          {(close) => (
            <div className="kc-row">
              <Btn label={t('서식 지우기')} icon={Eraser} disabled={!p.textActive}
                onClick={() => { p.onFormat('removeFormat'); close() }} />
            </div>
          )}
        </Menu>
      ) : (
        <Btn label={t('서식 지우기')} icon={Eraser} disabled={!p.textActive} onClick={() => p.onFormat('removeFormat')} />
      )}
      <Sep />
      <Menu label={t('디자인')} width={288} testid="design" align="right" button={<Palette size={16} />}>
        {p.design}
      </Menu>
      <span className="kc-fill" />
      <Btn label={p.reviewCount > 0 ? t('검토 · 확인 {count}').replace('{count}', String(p.reviewCount)) : t('검토')}
        icon={ShieldQuestion} pressed={p.reviewOpen} onClick={p.onReview}>
        {p.reviewCount > 0 ? <span className="kc-count">{p.reviewCount}</span> : null}
      </Btn>
      <Btn label={t('발표')} icon={Play} onClick={p.onPresent}>
        {narrow ? null : <span className="kc-word">{t('발표')}</span>}
      </Btn>
    </div>
  )
}
