// LibreChat 서버측 에러 wrapper 를 [KloudChat] 마커 붙은 메시지에서만 벗기기.
//
// 원본 동작
// ---------
// /app/api/server/controllers/agents/client.js (sendCompletion catch 블록):
//   `An error occurred while processing the request: <err.message>` 로 감쌈.
// → upstream LiteLLM/provider 에러 stack 이 사용자에게 그대로 노출.
//
// 패치
// ----
// err.message 에 `[KloudChat]` 마커 있으면 그 위치부터 슬라이스(=wrapper 제거).
// KloudChat 콜백 (예: truncate_to_ctx.py 의 _make_ctx_error) 이 던지는 정제된
// 한국어 에러 = 깨끗하게 노출, 다른 upstream 에러 = 기존 wrapper 그대로
// — 디버깅 컨텍스트 유지.
//
// 클라이언트측 wrapper("Something went wrong. Here's the specific...") =
// 런타임에 entrypoint 가 .orig 에서 복원 → 빌드시 패치 무효.
// 그쪽은 scripts/librechat-patch.py 의 unwrap_kloudchat_errors() 가 매 startup
// 마다 다시 박음. 책임 분담.

const fs = require('fs');

const SERVER_PATH = '/app/api/server/controllers/agents/client.js';
const MARKER_TAG = 'KLOUDCHAT_ERROR_UNWRAP_PATCH';
const KC_MARKER = '[KloudChat]';

const NEEDLE =
  "[ContentTypes.ERROR]: `An error occurred while processing the request${err?.message ? `: ${err.message}` : ''}`,";
const REPLACE =
  "[ContentTypes.ERROR]: (err?.message && err.message.indexOf('" + KC_MARKER + "') >= 0) " +
  "? err.message.slice(err.message.indexOf('" + KC_MARKER + "')) " +
  ": `An error occurred while processing the request${err?.message ? `: ${err.message}` : ''}`, // " +
  MARKER_TAG;

const src = fs.readFileSync(SERVER_PATH, 'utf8');
if (src.includes(MARKER_TAG)) {
  console.log('[patch_librechat_error_unwrap] already applied, skipping');
  process.exit(0);
}
if (!src.includes(NEEDLE)) {
  console.error('[patch_librechat_error_unwrap] NEEDLE not found — LibreChat upstream changed?');
  process.exit(1);
}
fs.writeFileSync(SERVER_PATH, src.replace(NEEDLE, REPLACE));
console.log('[patch_librechat_error_unwrap] applied');
