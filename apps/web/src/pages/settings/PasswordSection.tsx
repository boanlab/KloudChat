import { Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { PasswordRule, passwordProblem } from '@/components/auth/PasswordRule'
import { Button, Field, Input, RequiredNote } from '@/components/ui'
import { ApiError, errorMessage } from '@/lib/api'
import { checkRequired } from '@/lib/formCheck'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

type Key = 'current' | 'next' | 'confirm'

/** 비밀번호 변경, at the top of 보안: the rule is the server's, and a refusal names the field. */
export function PasswordSection() {
  const t = useT()
  const { changePassword, lang, passwordMinLength } = useStore()
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' })
  const [problem, setProblem] = useState<{ key: Key; message: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const refs = {
    current: useRef<HTMLInputElement>(null),
    next: useRef<HTMLInputElement>(null),
    confirm: useRef<HTMLInputElement>(null),
  }
  const { hash } = useLocation()
  // Arriving from 프로필's link: straight to the first field.
  useEffect(() => {
    if (hash !== '#password') return
    refs.current.current?.scrollIntoView?.({ block: 'center' })
    refs.current.current?.focus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hash])
  const errorFor = (key: Key) => (problem?.key === key ? problem.message : null)
  const edit = (key: Key, value: string) => {
    setPw({ ...pw, [key]: value })
    setDone(false)
    setFailed(null)
    if (problem?.key === key) setProblem(null)
  }

  const submit = async () => {
    if (busy) return
    const found =
      checkRequired<Key>(lang, [
        { key: 'current', label: t('현재 비밀번호'), value: pw.current, control: refs.current },
        { key: 'next', label: t('새 비밀번호'), value: pw.next, control: refs.next },
        { key: 'confirm', label: t('새 비밀번호 확인'), value: pw.confirm, control: refs.confirm },
      ]) ??
      (passwordProblem(t, pw.next, passwordMinLength)
        ? { key: 'next' as const, message: passwordProblem(t, pw.next, passwordMinLength) as string }
        : pw.next !== pw.confirm
          ? { key: 'confirm' as const, message: t('새 비밀번호가 일치하지 않습니다.') }
          : null)
    setProblem(found)
    if (found) {
      refs[found.key].current?.focus()
      return
    }
    setBusy(true)
    setFailed(null)
    try {
      await changePassword(pw.current, pw.next)
      setPw({ current: '', next: '', confirm: '' })
      setDone(true)
    } catch (err) {
      if (err instanceof ApiError && err.detail === 'wrong_current_password') {
        setProblem({ key: 'current', message: t('현재 비밀번호가 맞지 않습니다.') })
        refs.current.current?.focus()
      } else {
        setFailed(errorMessage(err, t('변경에 실패했습니다.')))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <section id="password" className="space-y-3" aria-labelledby="password-heading">
      <h3 id="password-heading" className="text-base font-medium">
        {t('비밀번호 변경')}
      </h3>
      <form
        className="space-y-3"
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <RequiredNote />
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t('현재 비밀번호')} required error={errorFor('current')}>
            <Input
              ref={refs.current}
              type="password"
              autoComplete="current-password"
              value={pw.current}
              onChange={(e) => edit('current', e.target.value)}
            />
          </Field>
          <Field
            label={t('새 비밀번호')}
            required
            error={errorFor('next')}
            hint={<PasswordRule value={pw.next} minLength={passwordMinLength} />}
          >
            <Input
              ref={refs.next}
              type="password"
              autoComplete="new-password"
              value={pw.next}
              onChange={(e) => edit('next', e.target.value)}
            />
          </Field>
          <Field label={t('새 비밀번호 확인')} required error={errorFor('confirm')}>
            <Input
              ref={refs.confirm}
              type="password"
              autoComplete="new-password"
              value={pw.confirm}
              onChange={(e) => edit('confirm', e.target.value)}
            />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" aria-busy={busy || undefined}>
            {busy && <Loader2 size={14} className="animate-spin" aria-hidden />}
            {busy ? t('변경 중…') : t('비밀번호 변경')}
          </Button>
          <span role="status" className="text-sm text-success">
            {done ? t('변경했습니다. 다른 기기의 로그인은 모두 해제되었습니다.') : ''}
          </span>
          {failed && (
            <span role="alert" className="text-sm text-danger">
              {failed}
            </span>
          )}
        </div>
      </form>
    </section>
  )
}
