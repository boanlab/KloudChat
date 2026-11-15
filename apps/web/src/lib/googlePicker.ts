/**
 * Google Picker (「드라이브에서 가져오기」): the person picks files in Google's own dialog; with
 * the `drive.file` scope only picked files become ones the app may open. The loader script
 * comes from apis.google.com and the dialog is a docs.google.com frame (security-headers.conf).
 */
import { ApiError, errorCode, integrationsApi, type FileRow } from '@/lib/api'
import { currentLang } from '@/lib/i18n'

/* Only the parts of the Picker API used here. */
type PickerData = { action: string; docs?: { id: string }[] }
type PickerBuilder = {
  setAppId(id: string): PickerBuilder
  setOAuthToken(token: string): PickerBuilder
  setDeveloperKey(key: string): PickerBuilder
  setOrigin(origin: string): PickerBuilder
  setLocale(locale: string): PickerBuilder
  addView(view: unknown): PickerBuilder
  enableFeature(feature: string): PickerBuilder
  setCallback(cb: (data: PickerData) => void): PickerBuilder
  build(): { setVisible(on: boolean): void }
}
type PickerNamespace = {
  PickerBuilder: new () => PickerBuilder
  DocsView: new (viewId?: string) => { setIncludeFolders(on: boolean): unknown; setSelectFolderEnabled(on: boolean): unknown }
  ViewId: { DOCS: string }
  Feature: { MULTISELECT_ENABLED: string }
  Action: { PICKED: string; CANCEL: string }
}
declare global {
  interface Window {
    gapi?: { load(name: string, done: () => void): void }
    google?: { picker?: PickerNamespace }
  }
}

const LOADER = 'https://apis.google.com/js/api.js'
let loading: Promise<PickerNamespace> | null = null

function loadPicker(): Promise<PickerNamespace> {
  if (window.google?.picker) return Promise.resolve(window.google.picker)
  loading ??= new Promise<PickerNamespace>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = LOADER
    script.async = true
    script.onerror = () => {
      loading = null
      reject(new Error('picker_unloadable'))
    }
    script.onload = () => window.gapi?.load('picker', () => (window.google?.picker ? resolve(window.google.picker) : reject(new Error('picker_unloadable'))))
    document.head.appendChild(script)
  })
  return loading
}

/** Ids of the files the person picked; empty when they closed the dialog. */
export async function pickDriveIds(): Promise<string[]> {
  const grant = await integrationsApi.picker()
  const picker = await loadPicker()
  return new Promise((resolve) => {
    const view = new picker.DocsView(picker.ViewId.DOCS)
    view.setIncludeFolders(false)
    view.setSelectFolderEnabled(false)
    new picker.PickerBuilder()
      .setAppId(grant.appId)
      .setOAuthToken(grant.accessToken)
      .setDeveloperKey(grant.apiKey)
      .setOrigin(window.location.origin)
      .setLocale(currentLang() === 'en' ? 'en' : 'ko')
      .addView(view)
      .enableFeature(picker.Feature.MULTISELECT_ENABLED)
      .setCallback((data) => {
        if (data.action === picker.Action.PICKED) resolve((data.docs ?? []).map((d) => d.id))
        else if (data.action === picker.Action.CANCEL) resolve([])
      })
      .build()
      .setVisible(true)
  })
}

/** What to say when picking failed, by the server's code. */
export function pickFailure(err: unknown): string {
  const code = err instanceof ApiError ? errorCode(err) : err instanceof Error ? err.message : ''
  if (code === 'not_connected' || code === 'reconnect_required' || code === 'scope_missing')
    return '설정 › 연결에서 Google 드라이브를 먼저 연결하세요.'
  if (code === 'picker_unavailable' || code === 'feature_off') return '관리자가 드라이브 가져오기를 켜지 않았습니다.'
  if (code === 'picker_unloadable') return 'Google 파일 선택 창을 열지 못했습니다. 잠시 뒤 다시 시도하세요.'
  return '드라이브에서 가져오지 못했습니다.'
}

/** Picks and brings the files in: project material with `projectId`, else attachments. */
export async function importFromDrive(target: { projectId?: string | null; sessionId?: string | null }): Promise<{
  files: FileRow[]
  refused: number
}> {
  const ids = await pickDriveIds()
  if (!ids.length) return { files: [], refused: 0 }
  const out = await integrationsApi.driveImport(ids, target)
  return { files: out.files, refused: out.refused.length }
}

/** Whether a stored file's source is a Drive or Docs address (a file brought in from Drive). */
export function isDriveLink(url: string | null | undefined): boolean {
  return /^https:\/\/(?:docs|drive)\.google\.com\//.test(url ?? '')
}
