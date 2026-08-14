/**
 * Service name and logo. With nothing configured by an administrator, the
 * fallback mark is built from the first character of the name.
 *
 * The sign-in screen renders before authentication, when the store may still
 * be empty, so this takes the values it needs as props.
 */
export function Brand({
  name,
  logo,
  size = 'sm',
}: {
  name: string
  logo?: string
  size?: 'sm' | 'md'
}) {
  const box = size === 'md' ? 'size-8 rounded-xl text-sm' : 'size-7 rounded-lg text-[13px]'
  const label = size === 'md' ? 'font-semibold tracking-tight' : 'text-sm font-semibold tracking-tight'

  return (
    <div className="flex items-center gap-2.5">
      {logo ? (
        <img
          src={logo}
          alt=""
          className={`${box} object-contain`}
          // If the logo file has gone, render nothing rather than a broken image.
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
      <span className={label}>{name}</span>
    </div>
  )
}
