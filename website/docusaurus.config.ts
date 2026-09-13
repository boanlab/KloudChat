import { themes as prismThemes } from 'prism-react-renderer';
import type { Config } from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// The user guide. A static build served alongside the product — it has nothing to do with the
// running service and never reads from it. The pages are the markdown under ./docs, so a docs
// change stays a markdown diff.
const config: Config = {
  title: 'KloudChat',
  tagline: '업무에 쓰는 AI를 조직이 직접 운영합니다',
  favicon: 'img/favicon.svg',
  future: { v4: true },

  // GitHub Pages 프로젝트 사이트로 나간다. 포크에서 빌드하면 워크플로가 그 포크의 주소를
  // 환경 변수로 넣어 주므로 이 파일은 손대지 않아도 된다.
  url: process.env.SITE_URL ?? 'https://boanlab.github.io',
  baseUrl: process.env.SITE_BASE_URL ?? '/KloudChat/',
  organizationName: 'boanlab',
  projectName: 'KloudChat',
  trailingSlash: false,

  // Relative links into the rest of the repository are not pages of this site; reported, not fatal.
  onBrokenLinks: 'warn',
  markdown: {
    format: 'detect',
    mermaid: true,
    hooks: { onBrokenMarkdownLinks: 'warn' },
  },
  themes: ['@docusaurus/theme-mermaid'],

  i18n: {
    defaultLocale: 'ko',
    locales: ['ko', 'en'],
    localeConfigs: { ko: { label: '한국어' }, en: { label: 'English' } },
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          showLastUpdateTime: false,
        },
        blog: false,
        theme: { customCss: './src/css/custom.css' },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: { respectPrefersColorScheme: true },
    navbar: {
      title: 'KloudChat',
      logo: { alt: 'KloudChat', src: 'img/logo.svg' },
      items: [
        { type: 'docSidebar', sidebarId: 'user', position: 'left', label: '사용자 가이드' },
        { type: 'docSidebar', sidebarId: 'admin', position: 'left', label: '관리자 가이드' },
        { type: 'localeDropdown', position: 'right' },
        { href: 'https://github.com/boanlab/KloudChat', label: 'GitHub', position: 'right' },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: '사용자 가이드',
          items: [
            { label: '가입과 로그인', to: '/docs/start/signup' },
            { label: '대화 기본', to: '/docs/chat/basics' },
            { label: '보고서 작성', to: '/docs/write/report' },
            { label: '개인정보 보호', to: '/docs/privacy' },
          ],
        },
        {
          title: '도움이 필요할 때',
          items: [
            { label: '제한 사항과 문제 해결', to: '/docs/limits' },
            { label: '자주 묻는 질문', to: '/docs/faq' },
            { label: '용어', to: '/docs/glossary' },
          ],
        },
        {
          title: '관리자',
          items: [
            { label: '관리자 가이드', to: '/docs/admin/' },
            { label: 'GitHub', href: 'https://github.com/boanlab/KloudChat' },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} KloudChat authors.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'powershell', 'python', 'json'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
