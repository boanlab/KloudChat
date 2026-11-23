/**
 * The Univer grid on one element: it shows the server's model, tells when the person changes
 * something, and says what changed since it was shown (`sheetData.diffWorkbook`).
 *
 * Own chunk (the panel is lazy). The community packages only; the app's content security policy
 * leaves the page nothing to call out to.
 */
import { UniverSheetsCorePreset } from '@univerjs/preset-sheets-core'
import SheetsEnUS from '@univerjs/preset-sheets-core/locales/en-US'
import SheetsKoKR from '@univerjs/preset-sheets-core/locales/ko-KR'
import '@univerjs/preset-sheets-core/lib/index.css'
import { createUniver, LocaleType } from '@univerjs/presets'
import type { SheetModel, SheetOp } from '@/lib/api'
import {
  baselineFrom, baselineOf, diffWorkbook, readWorkbook, toWorkbookData,
  type BaseSheet, type Structure,
} from './sheetData'

type Api = ReturnType<typeof createUniver>['univerAPI']
type Workbook = NonNullable<ReturnType<Api['getActiveWorkbook']>>

export interface SheetEditorApi {
  show: (model: SheetModel) => Promise<void>
  /** Ops for what changed since the model was shown (or last committed). */
  changes: () => { ops: SheetOp[]; commit: () => void }
  setEditable: (editable: boolean) => void
  setDark: (dark: boolean) => void
  destroy: () => void
}

/** Grid mutations that are shape changes, and the grid's own name for the sheet they happen on. */
const SHAPE: Record<string, { kind: Structure['kind']; rows: boolean }> = {
  'sheet.mutation.insert-row': { kind: 'insertRows', rows: true },
  'sheet.mutation.remove-rows': { kind: 'deleteRows', rows: true },
  'sheet.mutation.insert-col': { kind: 'insertCols', rows: false },
  'sheet.mutation.remove-col': { kind: 'deleteCols', rows: false },
}
/** Mutations that are not an edit of the document (the view, the formula engine's results). */
const NOT_EDITS = /^(?:sheet\.mutation\.(?:set-worksheet-active|set-scroll|set-selections?|set-frozen|toggle-gridlines|set-gridlines-color|set-tab-color)|formula\.|sheet\.operation\.)/

export function mountSheetEditor(
  host: HTMLElement,
  options: {
    lang: 'ko' | 'en'
    editable: boolean
    dark: boolean
    /** An edit by the person (not the formula engine, not a load). */
    onEdit: () => void
    /** Fires once the grid has drawn (and again after each `show`). */
    onDrawn?: () => void
  },
): SheetEditorApi {
  const ko = options.lang !== 'en'
  const { univer, univerAPI } = createUniver({
    locale: ko ? LocaleType.KO_KR : LocaleType.EN_US,
    locales: { [ko ? LocaleType.KO_KR : LocaleType.EN_US]: ko ? SheetsKoKR : SheetsEnUS },
    presets: [UniverSheetsCorePreset({ container: host, disableAutoFocus: true })],
  })
  const api = univerAPI as Api
  let baseline: BaseSheet[] = []
  let structure: Structure[] = []
  let loading = false
  let editable = options.editable

  const current = (): Workbook | null => api.getActiveWorkbook() ?? null

  const listen = (workbook: Workbook) =>
    workbook.onCommandExecuted((command) => {
      if (loading || !command.id.startsWith('sheet.')) return
      if (command.type !== 2 && command.type !== 0) return
      if (NOT_EDITS.test(command.id)) return
      const shape = SHAPE[command.id]
      const params = command.params as
        | { subUnitId?: string; range?: { startRow: number; endRow: number; startColumn: number; endColumn: number } }
        | undefined
      if (shape && params?.range && params.subUnitId && command.type === 2) {
        const range = params.range
        structure.push({
          kind: shape.kind,
          sheet: params.subUnitId,
          at: shape.rows ? range.startRow : range.startColumn,
          count: shape.rows ? range.endRow - range.startRow + 1 : range.endColumn - range.startColumn + 1,
        })
      }
      // a command is followed by its mutations: counting the mutations alone, once each
      if (command.type === 2) options.onEdit()
    })

  let listener: { dispose: () => void } | null = null

  /** What the grid holds now. */
  const read = () => {
    const workbook = current()
    if (!workbook) return null
    const snapshot = workbook.save() as unknown as Record<string, unknown>
    const now = readWorkbook(snapshot, (id, rows, cols) => {
      const sheet = workbook.getSheetBySheetId(id)
      return sheet ? sheet.getRange(0, 0, rows, cols).getFormulas() : []
    })
    return now
  }

  const api2: SheetEditorApi = {
    async show(model) {
      loading = true
      try {
        const at = current()?.getActiveSheet().getSheetName()
        listener?.dispose()
        const old = current()
        if (old) api.disposeUnit(old.getId())
        const data = toWorkbookData(model, ko ? 'koKR' : 'enUS', model.title)
        const workbook = api.createWorkbook(data as never)
        baseline = baselineOf(model)
        structure = []
        const back = at ? workbook.getSheetByName(at) : null
        if (back) workbook.setActiveSheet(back)
        workbook.setEditable(editable)
        listener = listen(workbook)
        // the formula engine settles its first results before an edit counts
        await new Promise((resolve) => window.setTimeout(resolve, 400))
      } finally {
        loading = false
      }
      options.onDrawn?.()
    },
    changes() {
      const now = read()
      if (!now) return { ops: [], commit: () => undefined }
      const ops = diffWorkbook(baseline, structure, now)
      const sent = structure.length
      return {
        ops,
        commit: () => {
          baseline = baselineFrom(now)
          // shape changes made after the reading stay to be sent
          structure = structure.slice(sent)
        },
      }
    },
    setEditable(next) {
      editable = next
      current()?.setEditable(next)
      api.setUIVisible(api.Enum.BuiltInUIPart.TOOLBAR, next)
    },
    setDark(dark) {
      api.toggleDarkMode(dark)
    },
    destroy() {
      listener?.dispose()
      univer.dispose()
    },
  }
  api.toggleDarkMode(options.dark)
  // the toolbar is the editing: a read-only view leaves only the grid and its sheet tabs
  api.setUIVisible(api.Enum.BuiltInUIPart.TOOLBAR, editable)
  return api2
}
