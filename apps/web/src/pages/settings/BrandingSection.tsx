import { Trash2, Upload } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Brand } from '@/components/layout/Brand'
import { Button, Field, Input } from '@/components/ui'
import { adminApi, type SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** Service name and logo; clearing the logo reverts to the default mark. */
export function BrandingSection({
  settings,
  onSaved,
}: {
  settings: SystemSettings | null
  onSaved: () => Promise<void>
}) {
  const t = useT()
  const refreshBrand = useStore((s) => s.refreshBrand)
  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (settings) {
      setName(settings.brand.name)
      setContact(settings.contact.source === 'database' ? settings.contact.email : '')
    }
  }, [settings])

  if (!settings) return null

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await onSaved()
      await refreshBrand()
    } catch (e) {
      setError(e instanceof Error ? e.message : t('실패했습니다.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-control border border-line bg-panel p-4">
        <p className="mb-3 text-sm text-muted">{t('미리보기')}</p>
        <Brand name={name.trim() || 'KloudChat'} logo={settings.brand.logo} size="md" />
      </div>

      <Field label={t('서비스 이름')} hint={t('사이드바와 로그인 화면에 표시됩니다.')}>
        <div className="flex items-center gap-2">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="KloudChat"
            maxLength={40}
          />
          <Button
            disabled={busy}
            title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
            onClick={() => void run(() => adminApi.updateSettings({ brandName: name.trim() }))}
          >
            {t('저장')}
          </Button>
        </div>
      </Field>

      <Field
        label={t('문의 주소')}
        hint={t('대기 화면의 「관리자에게 문의」가 여는 메일 주소입니다. 비우면 첫 관리자 계정의 주소를 씁니다.')}
      >
        <div className="flex items-center gap-2">
          <Input
            type="email"
            value={contact}
            onChange={(e) => setContact(e.target.value)}
            placeholder={settings.contact.email || 'admin@example.com'}
            className="font-mono text-base"
          />
          <Button
            disabled={busy}
            title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
            onClick={() => void run(() => adminApi.updateSettings({ contactEmail: contact.trim() }))}
          >
            {t('저장')}
          </Button>
        </div>
      </Field>

      <Field label={t('로고')} hint={t('PNG, JPG, WebP · 2MB 이하. 정사각형에 가까운 이미지가 가장 잘 맞습니다.')}>
        <div className="flex items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) void run(() => adminApi.uploadLogo(file))
            }}
          />
          <Button variant="ghost" disabled={busy}
            title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined} onClick={() => fileRef.current?.click()}>
            <Upload size={14} />
            {t('이미지 올리기')}
          </Button>
          {settings.brand.logo && (
            <Button variant="ghost" disabled={busy}
            title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined} onClick={() => void run(adminApi.deleteLogo)}>
              <Trash2 size={14} />
              {t('기본으로 되돌리기')}
            </Button>
          )}
        </div>
      </Field>

      {error && (
        <p className="rounded-control border border-danger/25 bg-danger/5 px-3 py-2 text-base text-danger">
          {error}
        </p>
      )}
    </div>
  )
}
