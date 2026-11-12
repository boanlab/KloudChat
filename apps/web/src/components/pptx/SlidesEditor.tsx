import {
  createImageElementFromFile, PptxI18nProvider, SlideCanvas, SlideThumbnail,
  useViewerBuildingBlocks,
  type PowerPointViewerHandle, type SlidesPaneProps, type ToolbarProps,
} from 'pptx-react-viewer'
import 'pptx-react-viewer/styles.css'
import { hasTextProperties, type PptxElement } from 'ooxml-core/pptx'
import {
  getInlineEditorSelectionResult, getSelectionTextStyleFlags, newTableElement, nextToggleValue,
  selectionBulletKind, stepFontSizePt, textFontSizePtToPx, textFontSizePxToPt,
} from 'ooxml-ui/pptx'
import {
  AlignCenter, AlignJustify, AlignLeft, AlignRight, ArrowDown, ArrowDownToLine, ArrowUp,
  ArrowUpToLine, Baseline, Bold, ChevronDown, ChevronUp, ClipboardPaste, Copy, CopyPlus,
  Image as ImageIcon, Italic, List, ListOrdered, Minus, MoreVertical, Plus, Redo2, Scissors,
  Shapes, Table2, Trash2, Type, Underline, Undo2, type LucideIcon,
} from 'lucide-react'
import {
  createElement, useCallback, useEffect, useMemo, useRef, useState,
  type MouseEvent as ReactMouseEvent, type ReactNode, type RefObject,
} from 'react'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'
import {
  charSelection, relabelHeader, setCharSelection, staggered, translateKo, wordAt,
  type CharRange,
} from './slideTools'
import './slides.css'

/** What the panel asks of the editor. */
export interface SlidesApi {
  getContent: () => Promise<Uint8Array>
  /** After a save: the edits so far are saved. */
  markClean: () => void
}

/** The two things the build adds to the building blocks' result (vite.config.ts). */
interface Added {
  updateNotes?: (text: string, segments: { text: string; style?: object; isParagraphBreak?: boolean }[]) => void
  markClean?: () => void
}

type Tp = ToolbarProps
type Element = PptxElement
type TableState = Tp['tableEditorState']
type Flags = ReturnType<typeof getSelectionTextStyleFlags>

/** Keeps toolbar clicks from taking the inline editor's selection. */
const keep = (event: ReactMouseEvent) => event.preventDefault()

const SHAPES: [string, string][] = [
  ['rect', '직사각형'], ['roundRect', '둥근 직사각형'], ['ellipse', '타원'], ['triangle', '삼각형'],
  ['diamond', '마름모'], ['rtArrow', '오른쪽 화살표'], ['chevron', '갈매기형'], ['star5', '별'],
]
const COLORS = [
  '#000000', '#434343', '#666666', '#999999', '#cccccc', '#ffffff', '#c00000', '#e69138',
  '#f1c232', '#6aa84f', '#45818e', '#3c78d8', '#3d85c6', '#674ea7', '#a64d79', '#e06666',
  '#ffd966', '#93c47d', '#6fa8dc', '#8e7cc3',
]
const SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 44, 48, 54, 60, 72, 96]
const BASE_FONTS = ['맑은 고딕', 'Pretendard', 'Noto Sans KR', 'Arial', 'Calibri', 'Times New Roman']
const ALIGNS: [string, LucideIcon, string][] = [
  ['left', AlignLeft, '왼쪽 맞춤'], ['center', AlignCenter, '가운데 맞춤'],
  ['right', AlignRight, '오른쪽 맞춤'], ['justify', AlignJustify, '양쪽 맞춤'],
]

// ── what the selection is ──

function textTarget(el: Element | null, table: TableState): boolean {
  if (!el) return false
  if (hasTextProperties(el)) return true
  return el.type === 'table' && !!table
}

function effectiveStyle(el: Element | null, table: TableState) {
  if (!el) return undefined
  if (hasTextProperties(el)) return el.textStyle
  if (el.type === 'table' && table && el.tableData) {
    return el.tableData.rows[table.rowIndex]?.cells[table.columnIndex]?.style
  }
  return undefined
}

/** B/I/U state at the selection, as the editor's own ribbon reads it. */
function readFlags(el: Element | null, table: TableState, live: boolean): Flags {
  if (el && hasTextProperties(el)) {
    const result = live ? getInlineEditorSelectionResult(el.textSegments) : null
    const current = result && result.kind === 'supported' &&
      (!result.snapshot || result.snapshot.elementId === el.id) ? result : null
    return getSelectionTextStyleFlags(
      current?.snapshot?.textSegments ?? el.textSegments, current?.selection ?? null, el.textStyle,
    )
  }
  // a cell's style reads as text style for these flags
  return getSelectionTextStyleFlags(undefined, null, effectiveStyle(el, table) as never)
}

function readBulletKind(el: Element | null): string {
  if (!el || !hasTextProperties(el)) return 'none'
  const result = getInlineEditorSelectionResult(el.textSegments, { preserveCaret: true })
  if (result.kind === 'supported' && (!result.snapshot || result.snapshot.elementId === el.id)) {
    return selectionBulletKind(el, result.selection, result.snapshot?.textSegments)
  }
  return selectionBulletKind(el, null)
}

/** The run style at the caret or the selection's start: font, size and colour shown. */
function caretRunStyle(el: Element | null): Record<string, unknown> {
  if (!el || !hasTextProperties(el)) return {}
  const result = getInlineEditorSelectionResult(el.textSegments)
  const segments = (result.kind === 'supported' && result.snapshot?.textSegments) ||
    el.textSegments || []
  const from = result.kind === 'supported' && result.selection ? result.selection.startSegIdx : 0
  const segment = segments.slice(from).find((s) => s.text) ?? segments.find((s) => s.text)
  return (segment?.style || {}) as Record<string, unknown>
}

// ── small parts ──

function Btn({
  label, icon: Icon, onClick, disabled, pressed, children,
}: {
  label: string
  icon?: LucideIcon
  onClick?: () => void
  disabled?: boolean
  pressed?: boolean
  children?: ReactNode
}) {
  return (
    <button
      type="button"
      className={'kc-btn' + (pressed ? ' on' : '')}
      title={label}
      aria-label={label}
      aria-pressed={pressed === undefined ? undefined : !!pressed}
      disabled={disabled}
      onMouseDown={keep}
      onClick={onClick}
    >
      {Icon ? <Icon size={16} strokeWidth={2} /> : null}
      {children}
    </button>
  )
}

function Menu({
  label, button, disabled, children, width, testid,
}: {
  label: string
  button: ReactNode
  disabled?: boolean
  children: (close: () => void) => ReactNode
  width?: number
  testid?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return undefined
    const away = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', away, true)
    return () => window.removeEventListener('mousedown', away, true)
  }, [open])
  return (
    <div className="kc-menu" ref={ref} data-testid={testid}>
      <button
        type="button"
        className={'kc-btn kc-drop' + (open ? ' on' : '')}
        title={label}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onMouseDown={keep}
        onClick={() => setOpen((was) => !was)}
      >
        {button}
        <ChevronDown size={12} />
      </button>
      {open && !disabled ? (
        <div className="kc-pop" role="menu" style={width ? { width } : undefined} onMouseDown={keep}>
          {children(() => setOpen(false))}
        </div>
      ) : null}
    </div>
  )
}

const Sep = () => <span className="kc-sep" aria-hidden="true" />

function TablePicker({ onPick, hint }: { onPick: (rows: number, cols: number) => void; hint: string }) {
  const [hover, setHover] = useState<[number, number]>([0, 0])
  return (
    <div className="kc-tpick">
      <div className="kc-tgrid" onMouseLeave={() => setHover([0, 0])}>
        {Array.from({ length: 6 * 8 }, (_, i) => {
          const r = Math.floor(i / 8) + 1
          const c = (i % 8) + 1
          return (
            <button
              key={i}
              type="button"
              className={'kc-cell' + (r <= hover[0] && c <= hover[1] ? ' on' : '')}
              aria-label={`${r}×${c}`}
              data-r={r}
              data-c={c}
              onMouseEnter={() => setHover([r, c])}
              onClick={() => onPick(r, c)}
            />
          )
        })}
      </div>
      <div className="kc-tlabel">{hover[0] ? `${hover[0]} × ${hover[1]}` : hint}</div>
    </div>
  )
}

// ── toolbar ──

function SlidesToolbar({
  tp, insert, width,
}: {
  tp: Tp
  insert: Inserts
  width: number
}) {
  const t = useT()
  const selected = tp.selectedElement
  const editable = tp.canEdit && tp.mode === 'edit'
  const isText = editable && textTarget(selected, tp.tableEditorState)
  const flags = readFlags(selected, tp.tableEditorState, true)
  const style = { ...(effectiveStyle(selected, tp.tableEditorState) || {}), ...caretRunStyle(selected) } as
    Record<string, unknown>
  const sizePt = typeof style.fontSize === 'number'
    ? Math.round(textFontSizePxToPt(style.fontSize) * 10) / 10 : null
  const font = String(style.eastAsiaFont || style.fontFamily || tp.themeFonts?.body || '')
  const align = (selected && hasTextProperties(selected) && selected.textStyle?.align) || 'left'
  const bullets = readBulletKind(selected)
  const fileRef = useRef<HTMLInputElement>(null)
  const fonts = useMemo(() => [...new Set([
    tp.themeFonts?.heading, tp.themeFonts?.body, ...(tp.embeddedFontFamilies || []), ...BASE_FONTS,
  ].filter((f): f is string => !!f))], [tp.themeFonts, tp.embeddedFontFamilies])
  const setStyle = (updates: Record<string, unknown>) => tp.onUpdateTextStyle(updates)
  const toggle = (flag: 'bold' | 'italic' | 'underline') =>
    setStyle({ [flag]: nextToggleValue(readFlags(selected, tp.tableEditorState, true)[flag]) })
  // align and lists move into ⋮ when the panel is narrow, the font list too when narrower
  const narrow = width < 680
  const tiny = width < 520

  const fontMenu = (closeOuter?: () => void) => (
    <Menu label={t('글꼴')} disabled={!isText} width={200} testid="font"
      button={<span className="kc-font">{font || t('글꼴')}</span>}>
      {(close) => fonts.map((face) => (
        <button key={face} type="button" className={'kc-item' + (face === font ? ' on' : '')}
          style={{ fontFamily: `'${face}', sans-serif` }}
          onClick={() => { setStyle({ fontFamily: face, eastAsiaFont: face }); close(); closeOuter?.() }}>
          {face}
        </button>
      ))}
    </Menu>
  )
  const paragraphControls = (closeOuter?: () => void) => (
    <>
      <Menu label={t('문단 정렬')} disabled={!isText} testid="align"
        button={createElement((ALIGNS.find((a) => a[0] === align) || ALIGNS[0])[1], { size: 16 })}>
        {(close) => (
          <div className="kc-row">
            {ALIGNS.map(([value, Icon, label]) => (
              <Btn key={value} label={t(label)} icon={Icon} pressed={align === value}
                onClick={() => { setStyle({ align: value }); close(); closeOuter?.() }} />
            ))}
          </div>
        )}
      </Menu>
      <Btn label={t('글머리 기호')} icon={List} disabled={!isText || selected?.type === 'table'}
        pressed={bullets === 'bullet'} onClick={() => tp.onToggleBullets('bullet')} />
      <Btn label={t('번호 매기기')} icon={ListOrdered} disabled={!isText || selected?.type === 'table'}
        pressed={bullets === 'numbered'} onClick={() => tp.onToggleBullets('numbered')} />
    </>
  )

  return (
    <div className="kc-toolbar" role="toolbar" aria-label={t('슬라이드 도구')}>
      <Btn label={t('실행 취소')} icon={Undo2} disabled={!tp.canUndo} onClick={tp.onUndo} />
      <Btn label={t('다시 실행')} icon={Redo2} disabled={!tp.canRedo} onClick={tp.onRedo} />
      <Menu label={t('확대/축소')} testid="zoom"
        button={<span className="kc-zoom">{Math.round(tp.scale * 100)}%</span>}>
        {(close) => (
          <>
            <button type="button" className="kc-item" onClick={() => { tp.onZoomToFit(); close() }}>
              {t('맞춤')}
            </button>
            {[50, 75, 100, 150, 200].map((zoom) => (
              <button key={zoom} type="button" className="kc-item"
                onClick={() => { insert.zoom(zoom / 100); close() }}>
                {zoom}%
              </button>
            ))}
          </>
        )}
      </Menu>
      <Sep />
      <Btn label={t('텍스트 상자')} icon={Type} disabled={!editable} onClick={insert.textBox} />
      <Menu label={t('도형')} disabled={!editable} width={176} testid="shape" button={<Shapes size={16} />}>
        {(close) => (
          <div className="kc-grid2">
            {SHAPES.map(([shape, label]) => (
              <button key={shape} type="button" className="kc-item" title={t(label)}
                onClick={() => { insert.shape(shape); close() }}>
                {t(label)}
              </button>
            ))}
          </div>
        )}
      </Menu>
      <Btn label={t('이미지')} icon={ImageIcon} disabled={!editable} onClick={() => fileRef.current?.click()} />
      <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden
        data-testid="image-input"
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) void insert.image(file)
        }} />
      <Menu label={t('표')} disabled={!editable} testid="table" button={<Table2 size={16} />}>
        {(close) => <TablePicker hint={t('표 크기 선택')} onPick={(r, c) => { insert.table(r, c); close() }} />}
      </Menu>
      <Sep />
      {!tiny && fontMenu()}
      <Btn label={t('글꼴 크기 줄이기')} icon={Minus} disabled={!isText}
        onClick={() => setStyle({ fontSize: textFontSizePtToPx(stepFontSizePt(sizePt || 18, 'decrease')) })} />
      <Menu label={t('글꼴 크기')} disabled={!isText} width={72} testid="size"
        button={<span className="kc-size">{sizePt ?? '–'}</span>}>
        {(close) => SIZES.map((size) => (
          <button key={size} type="button" className={'kc-item' + (size === sizePt ? ' on' : '')}
            onClick={() => { setStyle({ fontSize: textFontSizePtToPx(size) }); close() }}>
            {size}
          </button>
        ))}
      </Menu>
      <Btn label={t('글꼴 크기 늘리기')} icon={Plus} disabled={!isText}
        onClick={() => setStyle({ fontSize: textFontSizePtToPx(stepFontSizePt(sizePt || 18, 'increase')) })} />
      <Sep />
      <Btn label={t('굵게')} icon={Bold} disabled={!isText} pressed={isText && flags.bold === 'on'}
        onClick={() => toggle('bold')} />
      <Btn label={t('기울임꼴')} icon={Italic} disabled={!isText} pressed={isText && flags.italic === 'on'}
        onClick={() => toggle('italic')} />
      <Btn label={t('밑줄')} icon={Underline} disabled={!isText} pressed={isText && flags.underline === 'on'}
        onClick={() => toggle('underline')} />
      <Menu label={t('텍스트 색상')} disabled={!isText} width={232} testid="color"
        button={(
          <span className="kc-color">
            <Baseline size={16} />
            <i style={{ background: String(style.color || '#000') }} />
          </span>
        )}>
        {(close) => (
          <div className="kc-swatches">
            {COLORS.map((hex) => (
              <button key={hex} type="button" className="kc-sw" title={hex} aria-label={hex}
                style={{ background: hex }}
                onClick={() => { setStyle({ color: hex, colorRef: undefined }); close() }} />
            ))}
          </div>
        )}
      </Menu>
      {!narrow && (
        <>
          <Sep />
          {paragraphControls()}
        </>
      )}
      {narrow && (
        <Menu label={t('더보기')} testid="more" button={<MoreVertical size={16} />}>
          {(close) => (
            <div className="kc-row">
              {tiny && fontMenu(close)}
              {paragraphControls(close)}
            </div>
          )}
        </Menu>
      )}
    </div>
  )
}

// ── thumbnail rail ──

function Rail({ sp, compact, row }: { sp: SlidesPaneProps; compact: boolean; row: boolean }) {
  const t = useT()
  const [drag, setDrag] = useState<{ from: number; over: number } | null>(null)
  const [menu, setMenu] = useState<{ i: number; x: number; y: number } | null>(null)
  const width = row ? 112 : compact ? 92 : 148
  const active = sp.activeSlideIndex
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    listRef.current?.querySelector(`.kc-thumb[data-i="${active}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [active])
  useEffect(() => {
    if (!menu) return undefined
    const away = (event: MouseEvent) => {
      if (!(event.target as HTMLElement).closest?.('.kc-ctx')) setMenu(null)
    }
    window.addEventListener('mousedown', away, true)
    return () => window.removeEventListener('mousedown', away, true)
  }, [menu])
  const close = (run: () => void) => () => {
    run()
    setMenu(null)
  }
  return (
    <nav className="kc-rail" aria-label={t('슬라이드')}
      style={row ? undefined : { width: width + (compact ? 30 : 36) }}
      onKeyDown={(event) => {
        if (!sp.canEdit && !['ArrowDown', 'ArrowUp'].includes(event.key)) return
        if (event.key === 'Delete' || event.key === 'Backspace') {
          event.preventDefault()
          if (sp.slides.length > 1) sp.onDeleteSlides([active])
        } else if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
          event.preventDefault()
          sp.onSelectSlide(Math.min(sp.slides.length - 1, active + 1))
        } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
          event.preventDefault()
          sp.onSelectSlide(Math.max(0, active - 1))
        }
      }}>
      <div className="kc-rail-list" tabIndex={0} ref={listRef}>
        {sp.slides.map((slide, i) => (
          <div key={slide.id} data-i={i}
            className={'kc-thumb' + (i === active ? ' on' : '') + (drag && drag.over === i ? ' over' : '') +
              (slide.hidden ? ' hid' : '')}
            draggable={sp.canEdit}
            aria-current={i === active ? 'true' : undefined}
            onDragStart={() => setDrag({ from: i, over: i })}
            onDragOver={(event) => { event.preventDefault(); if (drag) setDrag({ ...drag, over: i }) }}
            onDrop={() => { if (drag && drag.from !== i) sp.onMoveSlide(drag.from, i); setDrag(null) }}
            onDragEnd={() => setDrag(null)}
            onClick={() => sp.onSelectSlide(i)}
            onContextMenu={(event) => {
              event.preventDefault()
              sp.onSelectSlide(i)
              if (sp.canEdit) setMenu({ i, x: event.clientX, y: event.clientY })
            }}>
            <span className="kc-num">{i + 1}</span>
            <div className="kc-tframe" style={{ width }}>
              <SlideThumbnail slide={slide} templateElements={sp.templateElementsBySlideId[slide.id] || []}
                canvasSize={sp.canvasSize} previewWidth={width} fieldContext={sp.fieldContext}
                tableStyleContext={sp.tableStyleContext} />
            </div>
            {sp.canEdit && (
              <button type="button" className="kc-tmore" aria-label={t('슬라이드 메뉴')}
                onClick={(event) => {
                  event.stopPropagation()
                  sp.onSelectSlide(i)
                  const box = event.currentTarget.getBoundingClientRect()
                  setMenu({ i, x: box.left, y: box.bottom })
                }}>
                <MoreVertical size={14} />
              </button>
            )}
          </div>
        ))}
      </div>
      {sp.canEdit && (
        <button type="button" className="kc-add" title={t('새 슬라이드')} aria-label={t('새 슬라이드')}
          onClick={() => sp.onAddSlideAfter(active)}>
          <Plus size={16} />
          {compact || row ? null : <span>{t('새 슬라이드')}</span>}
        </button>
      )}
      {menu && (
        <div className="kc-ctx" role="menu" style={{ left: menu.x, top: menu.y }}>
          <button type="button" role="menuitem" className="kc-item" onClick={close(() => sp.onAddSlideAfter(menu.i))}>
            <Plus size={14} />{t('새 슬라이드')}
          </button>
          <button type="button" role="menuitem" className="kc-item"
            onClick={close(() => sp.onDuplicateSlides([menu.i]))}>
            <Copy size={14} />{t('슬라이드 복제')}
          </button>
          <button type="button" role="menuitem" className="kc-item" disabled={menu.i === 0}
            onClick={close(() => sp.onMoveSlide(menu.i, menu.i - 1))}>
            <ArrowUp size={14} />{t('위로 이동')}
          </button>
          <button type="button" role="menuitem" className="kc-item" disabled={menu.i === sp.slides.length - 1}
            onClick={close(() => sp.onMoveSlide(menu.i, menu.i + 1))}>
            <ArrowDown size={14} />{t('아래로 이동')}
          </button>
          <button type="button" role="menuitem" className="kc-item danger" disabled={sp.slides.length < 2}
            onClick={close(() => sp.onDeleteSlides([menu.i]))}>
            <Trash2 size={14} />{t('슬라이드 삭제')}
          </button>
        </div>
      )}
    </nav>
  )
}

// ── notes ──

function Notes({
  slide, canEdit, update,
}: {
  slide: Tp['activeSlide']
  canEdit: boolean
  update?: Added['updateNotes']
}) {
  const t = useT()
  const [open, setOpen] = useState(true)
  const [draft, setDraft] = useState(slide?.notes || '')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const id = slide?.id
  useEffect(() => {
    setDraft(slide?.notes || '')
    // a new slide's notes, not every edit of these
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])
  const commit = (text: string) => {
    clearTimeout(timer.current)
    if (!update || text === (slide?.notes || '')) return
    update(text, text.split('\n').flatMap((line, i) => [
      ...(i ? [{ text: '\n', isParagraphBreak: true }] : []),
      ...(line ? [{ text: line, style: {} }] : []),
    ]))
  }
  return (
    <section className="kc-notes" aria-label={t('발표자 노트')}>
      <button type="button" className="kc-notes-bar" aria-expanded={open} onClick={() => setOpen((was) => !was)}>
        {open ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        <span>{t('발표자 노트')}</span>
      </button>
      {open && (
        <textarea className="kc-notes-text" value={draft} aria-label={t('발표자 노트')}
          placeholder={canEdit ? t('클릭하여 발표자 노트 추가') : ''}
          readOnly={!canEdit || !update}
          onChange={(event) => {
            const text = event.target.value
            setDraft(text)
            clearTimeout(timer.current)
            timer.current = setTimeout(() => commit(text), 600)
          }}
          onBlur={(event) => commit(event.target.value)} />
      )}
    </section>
  )
}

// ── canvas menu ──

interface CanvasMenu {
  x: number
  y: number
  element: boolean
}

function ElementMenu({ menu, tp, onClose }: { menu: CanvasMenu; tp: Tp; onClose: () => void }) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const away = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) onClose()
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('mousedown', away, true)
    window.addEventListener('keydown', escape, true)
    return () => {
      window.removeEventListener('mousedown', away, true)
      window.removeEventListener('keydown', escape, true)
    }
  }, [onClose])
  const item = (label: string, Icon: LucideIcon, run: () => void, disabled = false, danger = false) => (
    <button type="button" role="menuitem" className={'kc-item' + (danger ? ' danger' : '')} disabled={disabled}
      onClick={() => { run(); onClose() }}>
      <Icon size={14} />{t(label)}
    </button>
  )
  const has = menu.element && !!tp.selectedElement
  return (
    <div ref={ref} className="kc-ctx" role="menu" aria-label={t('도형 메뉴')} style={{ left: menu.x, top: menu.y }}>
      {item('잘라내기', Scissors, tp.onCut, !has)}
      {item('복사', Copy, tp.onCopy, !has)}
      {item('붙여넣기', ClipboardPaste, tp.onPaste, !tp.clipboardPayload)}
      {item('도형 복제', CopyPlus, tp.onDuplicate, !has)}
      <div className="kc-ctx-sep" role="separator" />
      {item('앞으로 가져오기', ArrowUp, () => tp.onMoveLayer('forward'), !has)}
      {item('맨 앞으로 가져오기', ArrowUpToLine, () => tp.onMoveLayerToEdge('front'), !has)}
      {item('뒤로 보내기', ArrowDown, () => tp.onMoveLayer('backward'), !has)}
      {item('맨 뒤로 보내기', ArrowDownToLine, () => tp.onMoveLayerToEdge('back'), !has)}
      <div className="kc-ctx-sep" role="separator" />
      {item('삭제', Trash2, tp.onDelete, !has, true)}
    </div>
  )
}

// ── inserts ──

interface Inserts {
  textBox: () => void
  shape: (shape: string) => void
  image: (file: File) => Promise<void>
  table: (rows: number, cols: number) => void
  zoom: (level: number) => void
}

// ── the editor ──

function Editor({
  content, initialSlide, canEdit, label, apiRef, onDirty, onReady, onError, onSlide,
}: {
  content: Uint8Array
  initialSlide: number
  canEdit: boolean
  label: string
  apiRef: RefObject<SlidesApi | null>
  onDirty: (dirty: boolean) => void
  onReady: () => void
  onError: (message: string) => void
  onSlide: (index: number) => void
}) {
  const t = useT()
  const handle = useRef<PowerPointViewerHandle | null>(null)
  const blocks = useViewerBuildingBlocks({
    content, canEdit, handle, googleFonts: false, allowExternalContent: false,
    autosaveEnabled: false, fitPadding: 16, onDirtyChange: onDirty, initialSlide,
    onActiveSlideChange: onSlide,
  } as Parameters<typeof useViewerBuildingBlocks>[0])
  const { toolbarProps: tp, canvasProps, slidesPaneProps: sp } = blocks
  const added = blocks as typeof blocks & Added
  const rootRef = useRef<HTMLDivElement>(null)
  /** The person last pressed inside the editor: its keys are the editor's. */
  const inside = useRef(false)

  useEffect(() => {
    apiRef.current = {
      getContent: async () => {
        if (!handle.current) throw new Error('editor not ready')
        return handle.current.getContent()
      },
      markClean: () => added.markClean?.(),
    }
    return () => {
      apiRef.current = null
    }
  })

  // ── the editor's typed-then-formatted text shows twice ("수정됨 수정됨") ──
  // Its inline editor re-renders its children from the model after a format change while the
  // browser-typed text nodes are still there, so text typed and then formatted shows (and is
  // committed) twice. When the editor's text differs from the model's, the canvas is mounted
  // anew in the same render as the change and the selection put back. While a Korean syllable
  // is being composed, the change waits for the composition to end.
  const [canvasKey, setCanvasKey] = useState(0)
  const restore = useRef<CharRange | null>(null)
  const composing = useRef(false)
  const queued = useRef<(() => void) | null>(null)
  const inlineEditor = () =>
    rootRef.current?.querySelector<HTMLElement>('.kc-canvas [data-inline-editor]') ?? null
  const withEditorSync = useCallback((apply: () => void) => {
    const editor = inlineEditor()
    if (!editor) {
      apply()
      return
    }
    if (composing.current) {
      queued.current = apply
      return
    }
    const domText = editor.textContent ?? ''
    const modelText = (tp.selectedElement as { text?: string } | null)?.text ?? ''
    let chars = charSelection(editor)
    // a caret formats the word it stands in, as Google Slides does, not the whole box
    if (chars && chars.start === chars.end) {
      const word = wordAt(domText, chars.start)
      if (!word) return
      chars = word
      setCharSelection(editor, chars)
    }
    apply()
    if (domText !== modelText && !editor.querySelector('[data-pptx-list-paragraph]')) {
      restore.current = chars
      setCanvasKey((key) => key + 1)
    }
  }, [tp])
  const formatText = useCallback(
    (updates: Parameters<Tp['onUpdateTextStyle']>[0]) =>
      withEditorSync(() => tp.onUpdateTextStyle(updates)),
    [withEditorSync, tp],
  )
  const toggleBullets = useCallback(
    (kind: 'bullet' | 'numbered') => withEditorSync(() => tp.onToggleBullets(kind)),
    [withEditorSync, tp],
  )
  useEffect(() => {
    let start: number | null = null
    const began = (event: CompositionEvent) => {
      const editor = (event.target as HTMLElement).closest?.<HTMLElement>('[data-inline-editor]')
      if (!editor) return
      composing.current = true
      start = charSelection(editor)?.start ?? null
    }
    // B/I/U pressed mid-syllable: applied to the syllables composed, once they are in
    const ended = () => {
      composing.current = false
      const apply = queued.current
      queued.current = null
      if (!apply) return
      setTimeout(() => {
        const editor = inlineEditor()
        const now = editor && charSelection(editor)
        if (editor && now && start !== null && now.end > start) {
          setCharSelection(editor, { start, end: now.end })
        }
        withEditorSync(apply)
      }, 0)
    }
    document.addEventListener('compositionstart', began, true)
    document.addEventListener('compositionend', ended, true)
    return () => {
      document.removeEventListener('compositionstart', began, true)
      document.removeEventListener('compositionend', ended, true)
    }
  }, [withEditorSync])
  useEffect(() => {
    if (!restore.current) return
    const chars = restore.current
    restore.current = null
    requestAnimationFrame(() => {
      const editor = inlineEditor()
      if (editor) {
        editor.focus()
        setCharSelection(editor, chars)
      }
    })
  }, [canvasKey])
  const tpx = useMemo(
    () => ({ ...tp, onUpdateTextStyle: formatText, onToggleBullets: toggleBullets }),
    [tp, formatText, toggleBullets],
  )

  // ── inserts: one undo step each, off the spot of an element already there; a table's
  // header row in the person's language ──
  const inserts = useMemo<Inserts>(() => {
    type Placed = { id: string; x: number; y: number; width: number; height: number }
    const add = (element: Element) => {
      const api = handle.current
      if (!api) return
      const others = api.getElements() as unknown as Placed[]
      const place = staggered({ ...(element as unknown as Placed), id: '' }, others, sp.canvasSize)
      api.addElement({ ...element, ...place, id: `kc-${crypto.randomUUID()}` } as Element)
    }
    return {
      textBox: () => add({
        id: '', type: 'text', x: 100, y: 100, width: 300, height: 60, text: '',
        textStyle: { fontSize: 24 },
      } as unknown as Element),
      shape: (shape) => add({
        id: '', type: 'shape', shapeType: shape,
        x: sp.canvasSize.width / 2 - 100, y: sp.canvasSize.height / 2 - 75, width: 200, height: 150,
        shapeStyle: { fillColor: '#4a86e8', strokeColor: '#3c78d8', strokeWidth: 1 },
      } as unknown as Element),
      image: async (file) => {
        const element = await createImageElementFromFile(file, sp.canvasSize)
        if (element) add(element)
      },
      table: (rows, cols) => {
        const table = newTableElement(rows, cols) as Element & { tableData?: object }
        const header = table.tableData ? relabelHeader(table.tableData as never, t('항목 {n}')) : null
        add((header ? { ...table, tableData: header } : table) as Element)
      },
      zoom: (level) => handle.current?.setZoom(level),
    }
  }, [sp.canvasSize, t])

  // ── right-click on the canvas: the element's menu ──
  const [menu, setMenu] = useState<CanvasMenu | null>(null)
  const closeMenu = useCallback(() => setMenu(null), [])
  const canvas = useMemo(() => ({
    ...canvasProps,
    onContextMenu: (id: string, event: ReactMouseEvent) => {
      canvasProps.onContextMenu(id, event)
      if (canEdit) setMenu({ x: event.clientX, y: event.clientY, element: true })
    },
    onCanvasContextMenu: (event: ReactMouseEvent) => {
      event.preventDefault()
      canvasProps.onCanvasContextMenu?.(event)
      if (canEdit) setMenu({ x: event.clientX, y: event.clientY, element: false })
    },
  }), [canvasProps, canEdit])

  const [width, setWidth] = useState(800)
  const [, setTick] = useState(0)
  useEffect(() => {
    const root = rootRef.current
    if (!root) return undefined
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(root)
    // the toolbar reads the DOM selection: it re-renders when that moves
    const moved = () => setTick((n) => n + 1)
    document.addEventListener('selectionchange', moved)
    const pressed = (event: PointerEvent) => {
      inside.current = root.contains(event.target as Node)
    }
    window.addEventListener('pointerdown', pressed, true)
    return () => {
      observer.disconnect()
      document.removeEventListener('selectionchange', moved)
      window.removeEventListener('pointerdown', pressed, true)
    }
  }, [])

  // Empty title and body places show what goes there, as PowerPoint and Google Slides do; the
  // editor itself draws an empty placeholder as nothing at all.
  const prompts = useMemo(() => {
    if (!canEdit) return ''
    const said: Record<string, string> = {
      title: t('제목을 입력하세요'), ctrtitle: t('제목을 입력하세요'),
      subtitle: t('부제목을 입력하세요'), body: t('텍스트를 입력하세요'),
      obj: t('텍스트를 입력하세요'),
    }
    const size = (kind: string) => Math.round((kind.includes('title') && kind !== 'subtitle' ? 28 : 16)
      * (tp.scale || 1))
    return (tp.activeSlide?.elements ?? []).flatMap((element) => {
      const kind = (element as { placeholderType?: string }).placeholderType ?? ''
      const text = (element as { text?: string }).text ?? ''
      if (!said[kind] || text.trim()) return []
      const at = `.kc-canvas [data-element-id=${JSON.stringify(element.id)}]`
      return [
        // an empty box has no text to take a click: the box itself takes it
        `${at}{outline:1px dashed #9aa0a6;outline-offset:-1px;pointer-events:auto}`,
        `${at}:not(:has([data-inline-editor]))::after{content:${JSON.stringify(said[kind])};` +
        'position:absolute;inset:0;display:flex;align-items:center;padding:0 12px;' +
        `color:#80868b;font-size:${size(kind)}px;pointer-events:none}`,
      ]
    }).join('\n')
  }, [tp.activeSlide, tp.scale, canEdit, t])

  const loaded = !blocks.loading && !blocks.error && sp.slides.length > 0
  useEffect(() => {
    if (loaded) onReady()
    else if (blocks.error) onError(blocks.error)
    // once per load
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, blocks.error])

  // Keys outside the inline editor: undo, redo, copy, cut, paste, duplicate, delete, B/I/U.
  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      const ours = rootRef.current?.contains(target) || (target === document.body && inside.current)
      if (!ours || !canEdit) return
      if (target.closest?.('[data-inline-editor],[contenteditable="true"],textarea,input,.kc-rail')) return
      const mod = event.ctrlKey || event.metaKey
      const key = event.key.toLowerCase()
      const element = tp.selectedElement
      const textSelected = !!window.getSelection()?.toString()
      const run = (go: () => void) => {
        event.preventDefault()
        go()
      }
      if (mod && key === 'z') run(() => (event.shiftKey ? tp.onRedo() : tp.onUndo()))
      else if (mod && key === 'y') run(tp.onRedo)
      else if (mod && key === 'c' && element && !textSelected) run(tp.onCopy)
      else if (mod && key === 'x' && element && !textSelected) run(tp.onCut)
      else if (mod && key === 'v' && tp.clipboardPayload) run(tp.onPaste)
      else if (mod && key === 'd' && element) run(tp.onDuplicate)
      else if ((event.key === 'Delete' || event.key === 'Backspace') && element) run(tp.onDelete)
      else if (mod && ['b', 'i', 'u'].includes(key) && element && textTarget(element, tp.tableEditorState)) {
        const flag = ({ b: 'bold', i: 'italic', u: 'underline' } as const)[key as 'b' | 'i' | 'u']
        run(() => tp.onUpdateTextStyle({
          [flag]: nextToggleValue(readFlags(element, tp.tableEditorState, false)[flag]),
        }))
      }
    }
    window.addEventListener('keydown', down)
    return () => window.removeEventListener('keydown', down)
  }, [tp, canEdit])

  if (blocks.error) {
    return <div className="kc-msg">{t('슬라이드를 열 수 없습니다')}: {blocks.error}</div>
  }
  const phone = width < 560
  const compact = width < 900
  return (
    <div ref={rootRef} role="region" aria-label={label}
      className={'kc-slides' + (compact ? ' compact' : '') + (phone ? ' phone' : '')}>
      {canEdit && (
        <div className="kc-top">
          <SlidesToolbar tp={tpx} insert={inserts} width={width} />
        </div>
      )}
      <div className="kc-body">
        {sp.slides.length ? <Rail sp={sp} compact={compact} row={phone} /> : null}
        <div className="kc-main">
          <div className="kc-canvas" data-pptx-scope="">
            {blocks.loading ? (
              <div className="kc-msg">{t('불러오는 중…')}</div>
            ) : (
              <SlideCanvas key={canvasKey} {...canvas} showRulers={false} onFormatText={formatText} />
            )}
          </div>
          <Notes slide={tp.activeSlide} canEdit={canEdit} update={added.updateNotes} />
        </div>
      </div>
      {menu && <ElementMenu menu={menu} tp={tp} onClose={closeMenu} />}
      {prompts && <style>{prompts}</style>}
    </div>
  )
}

/**
 * A `.pptx` in pptx-react-viewer's slide canvas and thumbnails under this app's own chrome, in
 * the manner of Google Slides: one toolbar row, a thumbnail rail, the speaker notes. Each new
 * `content` is a new editor (its undo history belongs to the version it opened).
 */
export function SlidesEditor({
  content, version, initialSlide = 0, canEdit, label, apiRef, onDirty, onReady, onError, onSlide,
}: {
  content: Uint8Array
  /** Changes with each file loaded. */
  version: number
  /** The slide shown first: a new version opens where the person was. */
  initialSlide?: number
  canEdit: boolean
  label: string
  apiRef: RefObject<SlidesApi | null>
  onDirty: (dirty: boolean) => void
  onReady: () => void
  onError: (message: string) => void
  onSlide?: (index: number) => void
}) {
  const lang = useStore((s) => s.lang)
  return (
    <PptxI18nProvider locale={lang === 'en' ? 'en' : 'ko'} translate={lang === 'en' ? english : translateKo}>
      <Editor key={version} content={content} initialSlide={initialSlide} canEdit={canEdit}
        label={label} apiRef={apiRef} onDirty={onDirty} onReady={onReady} onError={onError}
        onSlide={onSlide ?? noop} />
    </PptxI18nProvider>
  )
}

/** English: the editor's own words. */
const english = () => undefined
const noop = () => undefined
