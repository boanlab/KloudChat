import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';
const sidebars: SidebarsConfig = {
  user: [
    'index',
    {type: 'category', label: '시작하기', items: ['start/signup', 'start/home', 'start/shortcuts']},
    {type: 'category', label: '대화', items: ['chat/basics', 'chat/models', 'chat/files', 'chat/web-search', 'chat/connectors', 'chat/agents', 'chat/starters']},
    {type: 'category', label: '문서 작성', items: ['write/report', 'write/slides', 'write/editor', 'write/revise', 'write/media', 'write/artifacts', 'write/share']},
    {type: 'category', label: '개인 설정', items: ['personal/projects', 'personal/memory', 'personal/settings', 'personal/usage-history']},
    'privacy',
    'api',
    'limits',
    'faq',
    'glossary',
  ],
  admin: ['admin/index', 'admin/users', 'admin/policy', 'admin/system'],
};
export default sidebars;
