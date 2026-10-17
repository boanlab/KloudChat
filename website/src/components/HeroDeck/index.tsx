import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import useBaseUrl from '@docusaurus/useBaseUrl';
import { translate } from '@docusaurus/Translate';
import { CaretLeft, CaretRight } from '@phosphor-icons/react';

import styles from './styles.module.css';

/**
 * Hero screenshot carousel: one centred slide with its neighbours peeking at the sides;
 * moves only on arrow or key input (no autoplay, safe with reduced motion). Alt text kept
 * for screen readers.
 */

type Slide = { src: string; alt: string };

function useSlides(): Slide[] {
  // Hooks first, in a fixed order, so the table below stays plain data.
  const report = useBaseUrl('/img/guide/report.png');
  const slides = useBaseUrl('/img/guide/slides.png');
  const files = useBaseUrl('/img/guide/chat-with-file.png');
  const search = useBaseUrl('/img/guide/chat-websearch.png');
  const plain = useBaseUrl('/img/guide/chat-plain.png');
  return [
    {
      src: report,
      alt: translate({
        id: 'home.deck.report.alt',
        message: '보고서를 만드는 대화. 왼쪽은 대화, 오른쪽은 절 단위로 편집하는 보고서 패널이다.',
      }),
    },
    {
      src: slides,
      alt: translate({
        id: 'home.deck.slides.alt',
        message: '발표자료를 만드는 대화. 왼쪽은 대화, 오른쪽은 장 목록이 있는 슬라이드 패널이다.',
      }),
    },
    {
      src: files,
      alt: translate({
        id: 'home.deck.files.alt',
        message: '문서를 첨부해 내용을 찾는 대화. 답변 아래에 참고한 파일과 처리 내역이 표시된다.',
      }),
    },
    {
      src: search,
      alt: translate({
        id: 'home.deck.search.alt',
        message: '웹 검색을 켠 대화. 답변에 각주 번호가 붙고 확인한 출처가 함께 표시된다.',
      }),
    },
    {
      src: plain,
      alt: translate({
        id: 'home.deck.plain.alt',
        message: '자료 없이 묻는 대화. 답변 위에 무엇을 참고했는지가 처리 내역으로 표시된다.',
      }),
    },
  ];
}

export default function HeroDeck(): ReactNode {
  const slides = useSlides();
  const n = slides.length;
  const [at, setAt] = useState(0);
  const stage = useRef<HTMLDivElement>(null);

  const go = useCallback((step: number) => setAt((i) => (i + step + n) % n), [n]);

  // Arrow keys only while the stage has focus; page-wide keys untouched.
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
    };
    el.addEventListener('keydown', onKey);
    return () => el.removeEventListener('keydown', onKey);
  }, [go]);

  const prevLabel = translate({ id: 'home.deck.prev', message: '이전 화면' });
  const nextLabel = translate({ id: 'home.deck.next', message: '다음 화면' });

  return (
    <div className={styles.deck}>
      <div
        className={styles.stage}
        ref={stage}
        tabIndex={0}
        role="group"
        aria-roledescription={translate({ id: 'home.deck.role', message: '화면 모음' })}
        aria-label={translate({ id: 'home.deck.label', message: 'KloudChat 활용 화면' })}
      >
        {slides.map((s, i) => {
          // Ring distance, so last → first still slides sideways.
          let off = i - at;
          if (off > n / 2) off -= n;
          if (off < -n / 2) off += n;
          const near = Math.abs(off) <= 1;
          return (
            <figure
              key={i}
              className={styles.slide}
              style={{ '--off': off } as React.CSSProperties}
              data-state={off === 0 ? 'active' : near ? 'near' : 'far'}
              aria-hidden={off !== 0}
            >
              <img
                className={styles.shot}
                src={s.src}
                width={2880}
                height={1800}
                loading={i === 0 ? 'eager' : 'lazy'}
                decoding="async"
                alt={s.alt}
              />
            </figure>
          );
        })}

        <button type="button" className={`${styles.arrow} ${styles.arrowPrev}`} onClick={() => go(-1)} aria-label={prevLabel}>
          <CaretLeft size={20} weight="bold" aria-hidden="true" />
        </button>
        <button type="button" className={`${styles.arrow} ${styles.arrowNext}`} onClick={() => go(1)} aria-label={nextLabel}>
          <CaretRight size={20} weight="bold" aria-hidden="true" />
        </button>
      </div>

      <div className={styles.dots}>
        {slides.map((s, i) => (
          <button
            key={i}
            type="button"
            className={styles.dot}
            data-on={i === at}
            onClick={() => setAt(i)}
            aria-current={i === at}
            aria-label={translate(
              { id: 'home.deck.goTo', message: '{n}번째 화면으로' },
              { n: i + 1 },
            )}
          />
        ))}
      </div>
    </div>
  );
}
