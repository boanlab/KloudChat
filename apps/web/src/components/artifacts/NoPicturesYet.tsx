import { ImagePlus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * What the picker says when this account has no pictures yet.
 *
 * The control used to render only when there was something in it, which meant
 * the one path into a document was invisible to exactly the person who had not
 * found it: somebody who asked for a picture, got a document without one, and
 * had nothing on screen suggesting the feature exists. The writing model cannot
 * make a picture and `sanitise` drops every address that is not already inside
 * the file, so this control *is* the feature.
 *
 * Worse on a stock install, where the image surface is off by default: the
 * empty state is then permanent until an administrator turns it on, and saying
 * which is the difference between a dead end and an instruction.
 */
export function NoPicturesYet() {
  const t = useT()
  const enabledKinds = useStore((s) => s.enabledKinds)
  const imageOn = enabledKinds.includes('image')
  return (
    <div className="rounded-control border border-dashed border-line px-4 py-6 text-center">
      <p className="text-base text-muted">
        {imageOn
          ? t('아직 만든 그림이 없습니다. 이미지 화면에서 만들면 여기에 나타납니다.')
          : t('이미지 화면이 꺼져 있어 넣을 그림을 만들 수 없습니다. 관리자가 설정에서 켤 수 있습니다.')}
      </p>
      {imageOn && (
        <Link
          to="/new/image"
          className="mt-3 inline-flex items-center gap-1.5 text-base font-medium text-accent hover:underline"
        >
          <ImagePlus size={13} />
          {t('이미지 만들러 가기')}
        </Link>
      )}
    </div>
  )
}

