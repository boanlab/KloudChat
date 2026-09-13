import type { ReactNode } from 'react';
import useBaseUrl from '@docusaurus/useBaseUrl';
import Heading from '@theme/Heading';
import Translate, { translate } from '@docusaurus/Translate';
import { ShieldCheck, Sliders } from '@phosphor-icons/react';

import Reveal from '@site/src/components/Reveal';
import styles from './styles.module.css';

/**
 * 처음 쓰는 사람이 만나는 순서대로: 대화, 그 아래에 깔린 설정, 데이터가 나가는 경계, 그리고
 * 대화에서 곧장 나오는 문서.
 *
 * 네 칸 중 둘은 실제 화면 캡처를 싣고 둘은 아이콘을 싣는다. 넓은 칸과 좁은 칸이 번갈아 놓여
 * 같은 상자 네 개가 아니라 하나의 짜임으로 읽힌다.
 */

type Tile = {
  wide: boolean;
  title: ReactNode;
  description: ReactNode;
  shot?: { src: string; alt: string };
  icon?: ReactNode;
};

function useTiles(): Tile[] {
  // 훅을 먼저, 정해진 순서로 부른다. 아래 표는 그래야 그냥 데이터로 남는다.
  const chatShot = useBaseUrl('/img/guide/chat-with-file.png');
  const slidesShot = useBaseUrl('/img/guide/slides.png');
  return [
    {
      wide: true,
      title: <Translate id="home.f1.title">문서를 근거로 답합니다</Translate>,
      description: (
        <Translate id="home.f1.desc">
          PDF, 워드, 한글, 엑셀을 첨부하면 그 내용을 근거로 답변합니다. 첨부한 파일은 대화가 끝날
          때까지 참조되고, 분량이 많으면 질문과 관련된 부분만 골라 전달합니다.
        </Translate>
      ),
      shot: {
        src: chatShot,
        alt: translate({
          id: 'home.f1.shotAlt',
          message: '파일을 첨부한 대화. 답변 아래에 어떤 파일의 어느 부분을 참고했는지가 표시된다.',
        }),
      },
    },
    {
      wide: false,
      title: <Translate id="home.f2.title">반복 업무를 줄이는 설정</Translate>,
      description: (
        <Translate id="home.f2.desc">
          자료와 지침을 프로젝트로 묶고, 매번 알려야 할 내용은 메모리에 저장합니다. 역할이 정해진
          에이전트와 시작점을 쓰면 요청문을 다시 쓰지 않아도 됩니다.
        </Translate>
      ),
      icon: <Sliders size={30} weight="duotone" aria-hidden="true" />,
    },
    {
      wide: false,
      title: <Translate id="home.f3.title">데이터가 어디까지 나가는지 보입니다</Translate>,
      description: (
        <Translate id="home.f3.desc">
          모델마다 데이터 경계가 배지로 붙습니다. 개인정보가 감지되면 가려 보내거나 기관 안에서만
          도는 모델로 바꿀 수 있습니다.
        </Translate>
      ),
      icon: <ShieldCheck size={30} weight="duotone" aria-hidden="true" />,
    },
    {
      wide: true,
      title: <Translate id="home.f4.title">보고서와 발표자료로 끝냅니다</Translate>,
      description: (
        <Translate id="home.f4.desc">
          구성을 먼저 확인한 뒤 절 또는 장 단위로 작성합니다. 작성한 문서는 패널에서 직접 고치고,
          검토와 팩트체크를 거쳐 PDF, 워드, 한글, PowerPoint로 내보냅니다.
        </Translate>
      ),
      shot: {
        src: slidesShot,
        alt: translate({
          id: 'home.f4.shotAlt',
          message: '발표자료 패널. 왼쪽에 장 목록, 가운데에 슬라이드, 위쪽 리본에 편집과 검토 탭이 있다.',
        }),
      },
    },
  ];
}

export default function HomepageFeatures(): ReactNode {
  const tiles = useTiles();
  return (
    <section className={styles.features}>
      <div className="container">
        <div className={styles.grid}>
          {tiles.map((t, i) => (
            <Reveal
              key={i}
              delay={(i % 2) * 80}
              className={`${styles.cell} ${t.wide ? styles.cellWide : styles.cellNarrow}`}
            >
              <article className={styles.tile}>
                {t.icon && <span className={styles.tileIcon}>{t.icon}</span>}
                <Heading as="h3" className={styles.tileTitle}>
                  {t.title}
                </Heading>
                <p className={styles.tileDesc}>{t.description}</p>
                {t.shot && (
                  <div className={styles.tileShotFrame}>
                    <img
                      className={styles.tileShot}
                      src={t.shot.src}
                      width={2880}
                      height={1800}
                      loading="lazy"
                      alt={t.shot.alt}
                    />
                  </div>
                )}
              </article>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
