import type { ReactNode } from 'react'
import type { ReportTitleBlock } from '@/types'
import { BLANK, fieldRows, paperLineLabelled, titleBlockClass } from '@/components/report/docFormats'

/**
 * A document's head by purpose (`docFormats.ts`): cover page, field table, memo rows,
 * press header or paper header. The same markup `titleBlockHtml` writes for the paged
 * preview; `TITLE_BLOCK_CSS` styles both. A blank field shows 「(기입)」, muted; with
 * `onChange` every field is an input and a blank is its placeholder.
 */
export function TitleBlock({
  block,
  title,
  paged = false,
  onChange,
  titleNode,
}: {
  block: ReportTitleBlock
  title: string
  /** Inside a page view: a cover is also `.cover`, so it owns its page. */
  paged?: boolean
  /** Makes the fields editable; called with the whole block on every change. */
  onChange?: (next: ReportTitleBlock) => void
  /** The title element; the page editor passes its own editable line. */
  titleNode?: ReactNode
}) {
  const editable = Boolean(onChange)
  const setField = (index: number, value: string) => onChange?.({
    ...block,
    fields: block.fields.map((pair, at) => (at === index ? [pair[0], value] : pair)),
  })
  const value = (index: number) => {
    const [label, text] = block.fields[index]
    if (editable) {
      return <input className="tb-input" aria-label={label} value={text} placeholder={BLANK} onChange={(event) => setField(index, event.target.value)} />
    }
    return text.trim() ? text : <span className="tb-blank">{BLANK}</span>
  }
  const heading = titleNode ?? <h1 className="tb-title">{title}</h1>
  const subtitle = editable && block.subtitle !== undefined
    ? <p className="tb-subtitle"><input className="tb-input" aria-label="부제" value={block.subtitle} placeholder={BLANK} onChange={(event) => onChange?.({ ...block, subtitle: event.target.value })} /></p>
    : block.subtitle?.trim() ? <p className="tb-subtitle">{block.subtitle}</p> : null
  const label = block.label ? <p className="tb-label">{block.label}</p> : null
  const fieldsTable = (perRow: number) => block.fields.length > 0 && (
    <table className="tb-fields">
      <tbody>
        {fieldRows(block.fields.map((pair, index) => [String(index), pair[0]] as [string, string]), perRow).map((row) => (
          <tr key={row[0][0]}>
            {row.map(([index, key]) => [
              <th key={`k${index}`} scope="row">{key}</th>,
              <td key={`v${index}`}>{value(Number(index))}</td>,
            ])}
            {row.length < perRow && Array.from({ length: perRow - row.length }, (_, gap) => [<th key={`gk${gap}`} />, <td key={`gv${gap}`} />])}
          </tr>
        ))}
      </tbody>
    </table>
  )

  let inner: ReactNode
  if (block.head === 'cover') inner = <>{label}{heading}{subtitle}{fieldsTable(1)}</>
  else if (block.head === 'memo') {
    inner = <>{fieldsTable(1)}<div className="tb-memo-title"><span className="tb-key">제목</span>{heading}</div></>
  } else if (block.head === 'press') {
    inner = (
      <>
        <p className="tb-kicker">보도자료</p>
        {heading}
        {subtitle}
        <p className="tb-dateline">
          {block.fields.map(([key], index) => <span key={index}><b>{key}</b>{value(index)}</span>)}
        </p>
      </>
    )
  } else if (block.head === 'paper') {
    const keywords = block.keywords?.join(', ') ?? ''
    inner = (
      <>
        {heading}
        {subtitle}
        {block.fields.map(([key, text], index) => (
          <p key={index} className="tb-line">
            {editable || paperLineLabelled(key, text) ? <>{key}: {value(index)}</> : text}
          </p>
        ))}
        {block.abstract !== undefined && (
          <div className="tb-abstract">
            <span className="tb-key">초록</span>
            {editable
              ? <p><textarea className="tb-input" aria-label="초록" value={block.abstract} placeholder={BLANK} onChange={(event) => onChange?.({ ...block, abstract: event.target.value })} /></p>
              : <p>{block.abstract.trim() ? block.abstract : <span className="tb-blank">{BLANK}</span>}</p>}
          </div>
        )}
        {block.keywords !== undefined && (
          <p className="tb-keywords">
            <span className="tb-key">핵심어</span>{' '}
            {editable
              ? <input className="tb-input" aria-label="핵심어" value={keywords} placeholder={BLANK} onChange={(event) => onChange?.({ ...block, keywords: event.target.value.split(/\s*,\s*/) })} />
              : keywords.trim() ? keywords : <span className="tb-blank">{BLANK}</span>}
          </p>
        )}
      </>
    )
  } else inner = <>{label}{heading}{subtitle}{fieldsTable(2)}</>
  return <header className={titleBlockClass(block, paged)}>{inner}</header>
}
