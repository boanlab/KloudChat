import type { ReactNode } from 'react'
import type { ReportTitleBlock } from '@/types'
import { blankFor, draftRows, fieldRows, onePage, paperLineLabelled, titleBlockClass } from '@/components/report/docFormats'
import { useT } from '@/lib/useT'
import { isBlank } from '@/lib/placeholders'

/**
 * A document's head by purpose (`docFormats.ts`): cover page, field table, memo rows,
 * press header, paper header or 간이기안문 head with its 결재란. The same markup `titleBlockHtml` writes for the paged
 * preview; `TITLE_BLOCK_CSS` styles both. A blank field shows its name in brackets (「[작성자]」), marked; with
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
  const t = useT()
  const editable = Boolean(onChange)
  const setField = (index: number, value: string) => onChange?.({
    ...block,
    fields: block.fields.map((pair, at) => (at === index ? [pair[0], value] : pair)),
  })
  const value = (index: number) => {
    const [label, text] = block.fields[index]
    if (editable) {
      return <input className="tb-input" aria-label={label} value={text} placeholder={blankFor(label)} onChange={(event) => setField(index, event.target.value)} />
    }
    return text.trim() && !isBlank(text) ? text : <span className="tb-blank doc-placeholder">{text.trim() || blankFor(label)}</span>
  }
  const heading = titleNode ?? <h1 className="tb-title">{title}</h1>
  const subtitle = editable && block.subtitle !== undefined
    ? <p className="tb-subtitle"><input className="tb-input" aria-label="부제" value={block.subtitle} placeholder={blankFor('부제')} onChange={(event) => onChange?.({ ...block, subtitle: event.target.value })} /></p>
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
  if (onePage(block)) {
    // The 1쪽 보고: the title boxed, the head fields one line under it, right.
    inner = (
      <>
        <div className="tb-box">{heading}</div>
        {block.fields.length > 0 && (
          <p className="tb-fieldline">
            {block.fields.map(([key], index) => <span key={index}><b>{key}</b> {value(index)}</span>)}
          </p>
        )}
      </>
    )
  } else if (block.head === 'cover') inner = <>{label}{heading}{subtitle}{fieldsTable(1)}</>
  else if (block.head === 'memo') {
    inner = <>{fieldsTable(1)}<div className="tb-memo-title"><span className="tb-key">제목</span>{heading}</div></>
  } else if (block.head === 'draft') {
    inner = (
      <>
        <table className="tb-fields" aria-label={t('결재란')}>
          <tbody>
            {draftRows(block.fields).map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => {
                  const signer = cell.kind === 'approver' || cell.kind === 'sign' || cell.kind === 'signer'
                  const className = [signer && 'tb-signer', cell.kind === 'sign' && 'tb-sign'].filter(Boolean).join(' ') || undefined
                  if (cell.kind === 'label' || cell.kind === 'approver') return <th key={c} scope={cell.kind === 'label' ? 'row' : 'col'} className={className}>{cell.text}</th>
                  if ((cell.kind === 'value' || cell.kind === 'signer') && cell.field !== undefined) return <td key={c} className={className}>{value(cell.field)}</td>
                  return <td key={c} className={className} rowSpan={cell.rowSpan} />
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="tb-memo-title"><span className="tb-key">제목</span>{heading}</div>
      </>
    )
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
              ? <p><textarea className="tb-input" aria-label="초록" value={block.abstract} placeholder={blankFor('초록')} onChange={(event) => onChange?.({ ...block, abstract: event.target.value })} /></p>
              : <p>{block.abstract.trim() ? block.abstract : <span className="tb-blank doc-placeholder">{blankFor('초록')}</span>}</p>}
          </div>
        )}
        {block.keywords !== undefined && (
          <p className="tb-keywords">
            <span className="tb-key">핵심어</span>{' '}
            {editable
              ? <input className="tb-input" aria-label="핵심어" value={keywords} placeholder={blankFor('핵심어')} onChange={(event) => onChange?.({ ...block, keywords: event.target.value.split(/\s*,\s*/) })} />
              : keywords.trim() ? keywords : <span className="tb-blank doc-placeholder">{blankFor('핵심어')}</span>}
          </p>
        )}
      </>
    )
  } else inner = <>{label}{heading}{subtitle}{fieldsTable(2)}</>
  return <header className={titleBlockClass(block, paged)}>{inner}</header>
}
