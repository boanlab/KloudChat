/** Service name and logo; props, since sign-in renders before the store fills. */
export function Brand({
  name,
  logo,
  size = 'sm',
  markOnly = false,
}: {
  name: string
  logo?: string
  size?: 'sm' | 'md'
  /** Mark only, for the rail. */
  markOnly?: boolean
}) {
  const box = size === 'md' ? 'size-8 rounded-card text-md' : 'size-7 rounded-control text-base'
  const label = size === 'md' ? 'font-semibold tracking-tight' : 'text-base font-semibold tracking-tight'

  return (
    <div className="flex items-center gap-2.5">
      {logo ? (
        <img
          src={logo}
          alt=""
          className={`${box} object-contain`}
          // Missing logo file: nothing, not a broken image.
          onError={(e) => {
            e.currentTarget.style.display = 'none'
          }}
        />
      ) : (
        <div
          className={`${box} grid place-items-center bg-accent font-bold text-accent-fg`}
          aria-hidden
        >
          {(name.trim()[0] ?? 'K').toUpperCase()}
        </div>
      )}
      {!markOnly && <span className={label}>{name}</span>}
    </div>
  )
}
