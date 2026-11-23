/** Also the `/new/:kind` entry. */

/** The start's heading: who or what, and one line of what to do. */
export function StartHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="mt-1 text-base text-muted">{subtitle}</p>
    </>
  )
}
