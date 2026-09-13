import type { ReactNode } from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import Translate, { translate } from '@docusaurus/Translate';
import { ArrowRight, BookOpen, ChatCircleText, FileText, ShieldCheck } from '@phosphor-icons/react';

import HeroDeck from '@site/src/components/HeroDeck';
import HomepageFeatures from '@site/src/components/HomepageFeatures';
import Reveal from '@site/src/components/Reveal';
import styles from './index.module.css';

/**
 * 첫 페이지. 세 가지 규칙을 따른다.
 *
 * 1. 제품을 보여 준다. 화면이 곧 사용법이므로 실제 캡처가 지면을 끌고 가고 설명은 짧게 둔다.
 *    그린 그림은 한 장도 쓰지 않는다.
 * 2. 밝은 화면과 어두운 화면 모두 한 가지 바탕으로 간다. 히어로가 제 색을 강제하지 않고 사이트
 *    테마를 따르므로, 읽다가 다른 사이트로 넘어온 느낌이 나지 않는다.
 * 3. 화면에 보이는 모든 문자열은 Translate를 거친다. 영어는 i18n/en/code.json에 있다.
 */

function Hero() {
  return (
    <header className={styles.hero}>
      <div className={styles.heroGlow} aria-hidden="true" />
      <div className={`container ${styles.heroInner}`}>
        <div className={styles.heroCopy}>
          <Heading as="h1" className={styles.heroTitle}>
            <Translate id="home.hero.title">업무에 쓰는 AI를 조직이 직접 운영합니다</Translate>
          </Heading>
          <p className={styles.heroSub}>
            <Translate id="home.hero.sub">
              가지고 있는 문서를 첨부하면 그 내용을 근거로 답합니다. 같은 대화에서 보고서와
              발표자료를 만들고, 무엇을 참고했는지와 데이터가 어디까지 나갔는지를 함께 보여 줍니다.
            </Translate>
          </p>
          <div className={styles.heroActions}>
            <Link className={styles.ctaPrimary} to="/docs/">
              <Translate id="home.userGuide">사용자 가이드</Translate>
              <ArrowRight size={18} weight="duotone" aria-hidden="true" />
            </Link>
            <Link className={styles.ctaGhost} to="/docs/admin/">
              <Translate id="home.adminGuide">관리자 가이드</Translate>
            </Link>
          </div>
        </div>

        <HeroDeck />

      </div>
    </header>
  );
}

function Formats() {
  return (
    <section className={styles.quickstart}>
      <div className={`container ${styles.quickstartInner}`}>
        <Reveal className={styles.revealItem}>
          <Heading as="h2" className={styles.sectionTitle}>
            <Translate id="home.formats.title">다루는 파일과 내보내는 형식</Translate>
          </Heading>
          <p className={styles.sectionLede}>
            <Translate id="home.formats.lede">
              가지고 있는 자료를 변환 없이 그대로 올리고, 필요한 형식으로 받아 갑니다. 파일 하나는
              200MB까지입니다.
            </Translate>
          </p>
        </Reveal>
        <Reveal className={styles.revealItem} delay={90}>
          <div className={styles.formats}>
            <div className={styles.formatCol}>
              <h3 className={styles.formatHead}>
                <Translate id="home.formats.inHead">첨부할 수 있는 파일</Translate>
              </h3>
              <dl className={styles.formatList}>
                <dt>
                  <Translate id="home.formats.in1.k">문서</Translate>
                </dt>
                <dd>
                  <Translate id="home.formats.in1.v">PDF, Word, 한글, PowerPoint, Excel</Translate>
                </dd>
                <dt>
                  <Translate id="home.formats.in2.k">텍스트·코드</Translate>
                </dt>
                <dd>
                  <Translate id="home.formats.in2.v">
                    txt, md, csv, json과 대부분의 코드 파일
                  </Translate>
                </dd>
                <dt>
                  <Translate id="home.formats.in3.k">이미지</Translate>
                </dt>
                <dd>
                  <Translate id="home.formats.in3.v">
                    PNG, JPG, GIF, WebP. 이미지를 읽는 모델에서만
                  </Translate>
                </dd>
                <dt>
                  <Translate id="home.formats.in4.k">음성·영상</Translate>
                </dt>
                <dd>
                  <Translate id="home.formats.in4.v">mp3, wav, mp4. 전사가 연결된 경우</Translate>
                </dd>
              </dl>
            </div>
            <div className={styles.formatCol}>
              <h3 className={styles.formatHead}>
                <Translate id="home.formats.outHead">내보낼 수 있는 형식</Translate>
              </h3>
              <dl className={styles.formatList}>
                <dt>
                  <Translate id="home.formats.out1.k">보고서</Translate>
                </dt>
                <dd>
                  <Translate id="home.formats.out1.v">PDF, Word, 한글, 마크다운</Translate>
                </dd>
                <dt>
                  <Translate id="home.formats.out2.k">발표자료</Translate>
                </dt>
                <dd>
                  <Translate id="home.formats.out2.v">
                    PowerPoint, PDF, 노트를 포함한 텍스트
                  </Translate>
                </dd>
              </dl>
              <p className={styles.commandNote}>
                <Translate
                  id="home.formats.note"
                  values={{
                    link: (
                      <Link to="/docs/chat/files">
                        <Translate id="home.formats.noteLink">파일 첨부</Translate>
                      </Link>
                    ),
                  }}
                >
                  {'스캔한 이미지와 구형 오피스 형식(.doc, .ppt, .xls)은 읽지 못합니다. 자세한 조건은 {link}에 있습니다.'}
                </Translate>
              </p>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

type DocLink = { to: string; icon: ReactNode; title: ReactNode; desc: ReactNode };

const DOC_LINKS: DocLink[] = [
  {
    to: '/docs/start/signup',
    icon: <BookOpen size={22} weight="duotone" aria-hidden="true" />,
    title: <Translate id="home.map.start.title">시작하기</Translate>,
    desc: <Translate id="home.map.start.desc">가입과 로그인, 화면 구성, 단축키</Translate>,
  },
  {
    to: '/docs/chat/basics',
    icon: <ChatCircleText size={22} weight="duotone" aria-hidden="true" />,
    title: <Translate id="home.map.chat.title">대화</Translate>,
    desc: <Translate id="home.map.chat.desc">모델 선택, 파일 첨부, 웹 검색</Translate>,
  },
  {
    to: '/docs/write/report',
    icon: <FileText size={22} weight="duotone" aria-hidden="true" />,
    title: <Translate id="home.map.write.title">문서 작성</Translate>,
    desc: <Translate id="home.map.write.desc">보고서, 발표자료, 편집기, 내보내기</Translate>,
  },
  {
    to: '/docs/admin/',
    icon: <ShieldCheck size={22} weight="duotone" aria-hidden="true" />,
    title: <Translate id="home.map.admin.title">관리자 가이드</Translate>,
    desc: <Translate id="home.map.admin.desc">사용자 승인, 모델 정책, 시스템 설정</Translate>,
  },
];

function DocMap() {
  return (
    <section className={styles.docMap}>
      <div className="container">
        <ul className={styles.docList}>
          {DOC_LINKS.map((d, i) => (
            <li key={d.to}>
              <Reveal className={styles.revealItem} delay={i * 70}>
                <Link className={styles.docItem} to={d.to}>
                  <span className={styles.docIcon}>{d.icon}</span>
                  <span className={styles.docText}>
                    <span className={styles.docTitle}>{d.title}</span>
                    <span className={styles.docDesc}>{d.desc}</span>
                  </span>
                  <ArrowRight
                    className={styles.docArrow}
                    size={16}
                    weight="duotone"
                    aria-hidden="true"
                  />
                </Link>
              </Reveal>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

export default function Home(): ReactNode {
  const { siteConfig } = useDocusaurusContext();
  return (
    <Layout title={siteConfig.title} description={siteConfig.tagline}>
      <Hero />
      <main>
        <HomepageFeatures />
        <Formats />
        <DocMap />
      </main>
    </Layout>
  );
}
