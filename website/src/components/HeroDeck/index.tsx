import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import useBaseUrl from '@docusaurus/useBaseUrl';
import { translate } from '@docusaurus/Translate';
import { CaretLeft, CaretRight } from '@phosphor-icons/react';

import styles from './styles.module.css';

/**
 * 히어로의 화면 모음. 가운데 한 장이 서고 양옆에 다음 장이 조금 걸쳐 보이며, 화살표로 돌린다.
 *
 * 한 장씩 보여 주는 것보다 이 편이 나은 이유는, 첫 화면에서 "여러 가지를 할 수 있다"는 사실이
 * 설명 없이 보이기 때문이다. 양옆의 조각이 아직 안 본 화면이 남아 있다는 표시가 된다.
 *
 * 글로 붙이는 설명은 두지 않는다. 화면 자체가 설명이고, 바로 아래 네 칸에서 같은 내용을 글로
 * 다시 다룬다. 대체 텍스트는 화면 낭독기를 위해 남긴다.
 *
 * 자동으로 넘기지 않는다. 읽는 사람이 누를 때만 움직이므로 글을 읽는 동안 화면이 바뀌지 않고,
 * 모션을 줄이는 설정을 켠 사람에게도 그대로 쓸 수 있다.
 */

type Slide = { src: string; alt: string };

function useSlides(): Slide[] {
  // 훅을 먼저, 정해진 순서로 부른다. 아래 표는 그래야 그냥 데이터로 남는다.
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

  // 무대에 초점이 있을 때만 좌우 키를 받는다. 페이지 전체의 키 입력을 가로채지 않는다.
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
          // 고리처럼 이어 붙인 거리. 마지막 장에서 첫 장으로 넘어갈 때도 옆으로 흐른다.
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
