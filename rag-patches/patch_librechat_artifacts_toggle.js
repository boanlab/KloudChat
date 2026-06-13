// 저장된(DB) 에이전트도 채팅 Artifacts 토글(배지) 따르게 하기.
//
// 원본 동작
// ---------
// LibreChat artifacts 시스템 프롬프트 = @librechat/api 의 initializeAgentOptions
// (packages/api/dist/index.js) 에서 `agent.artifacts` 가 빈 문자열 아니면 무조건 주입.
//   - 커스텀 endpoint(ephemeral) 대화: loadEphemeralAgent 가 req.body.ephemeralAgent.artifacts
//     (= 채팅 Artifacts 배지 토글 값) 를 agent.artifacts 로 채움 → 토글 그대로 적용.
//   - 저장된 DB 에이전트(Super Agent/모델 에이전트) 대화: agent.artifacts 가 DB 저장값 그대로 →
//     배지 토글 무시. (agents endpoint 의 buildOptions 가 override 안 함.)
//
// 패치
// ----
// agents/build.js 의 buildOptions 반환 agent(Promise) 후처리 → 요청에
// ephemeralAgent.artifacts(문자열) 있으면 그 값으로 agent.artifacts 덮어쓰기.
// 이 agent 객체 = initialize.js 가 await 후 그대로 initializeAgent 에 전달(같은 참조) →
// 이후 프롬프트 게이팅이 override 된 값 읽음. 프롬프트 생성 자체는 기존 로직 담당.
//
//   배지 ON          → agent.artifacts = 'default' (override)  → 내장 프롬프트 주입
//   배지 OFF/미전송   → agent 저장값(DB) 유지
//
// manage.sh = image/ppt/video 를 artifacts:'default'(기본 ON), 그 외는 ''(off)로 sync.
// 따라서 Image/Slide/Video Studio = 기본 ON, 나머지는 토글로 ON. (배지가 force-on 만
// 하므로 기본 ON 인 3종은 배지로 끄지 못함 — 아티팩트 산출 에이전트라 의도된 동작.)

const fs = require('fs');

const SERVER_PATH = '/app/api/server/services/Endpoints/agents/build.js';
const MARKER_TAG = 'KLOUDCHAT_ARTIFACTS_TOGGLE_PATCH';

// artifacts ON 시 모델이 :::artifact 즉시 출력 않고 execute_code/web_search/
// fetch 로 빌드·실행·반복하다 tool 루프에 빠지는 것 차단하는 anti-loop 가이드. artifact =
// 프롬프트 아닌 토글로만 트리거 → 이 가이드도 토글 켜진 요청에만 주입.
// agent.instructions 에 append (initializeAgent 는 instructions 덮어쓰지 않고
// replaceSpecialVars 만 적용. additional_instructions=내장 artifacts 프롬프트와 별개).
const ARTIFACT_GUIDE =
  '\n\n[Artifact rule] When the request calls for an artifact (a self-contained ' +
  'HTML page, React/JSX component, diagram, or interactive widget), output the COMPLETE ' +
  'artifact in a single :::artifact{...} block written directly from your own knowledge. ' +
  'Do NOT call execute_code, web_search, fetch_url, or any tool to build, run, test, ' +
  'iterate on, or save the artifact — just write the full artifact code and finish. ' +
  '아티팩트는 도구로 빌드/실행/반복하지 말고 :::artifact 블록으로 한 번에 즉시 완성한다.';

const NEEDLE = '    agent: agentPromise,';
const REPLACE = [
  '    agent: agentPromise.then((__a) => {',
  '      // ' + MARKER_TAG + ': 대화 Artifacts 배지(ephemeralAgent.artifacts)가 명시적 ON',
  '      // (non-empty)이면 override(force-on). 배지 OFF(\'\')/미전송이면 agent 저장값 유지 —',
  '      // image/ppt/video 는 manage.sh 가 \'default\'(기본 ON)로, 그 외는 \'\'(off).',
  '      try {',
  '        const __ea = req && req.body && req.body.ephemeralAgent;',
  '        const __badge = (__ea && typeof __ea.artifacts === \'string\') ? __ea.artifacts : \'\';',
  '        if (__a && __badge) { __a.artifacts = __badge; }',
  '        if (__a && __a.artifacts) {',
  '          __a.instructions = (__a.instructions || \'\') + ' + JSON.stringify(ARTIFACT_GUIDE) + ';',
  '        }',
  '      } catch (__e) { /* override 실패는 무시 — 원본 동작 유지 */ }',
  '      return __a;',
  '    }),',
].join('\n');

const src = fs.readFileSync(SERVER_PATH, 'utf8');
if (src.includes(MARKER_TAG)) {
  console.log('[patch_librechat_artifacts_toggle] already applied, skipping');
  process.exit(0);
}
if (!src.includes(NEEDLE)) {
  console.error('[patch_librechat_artifacts_toggle] NEEDLE not found — LibreChat upstream changed?');
  process.exit(1);
}
const out = src.replace(NEEDLE, REPLACE);
fs.writeFileSync(SERVER_PATH, out);
console.log('[patch_librechat_artifacts_toggle] applied');
