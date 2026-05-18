// LibreChat 의 stable-diffusion 툴을 'generate_image' 으로 rename + 스키마에 'model' enum
// 필드와 override_settings 주입. 영향: StableDiffusion.js, handleTools.js, manifest.json,
// 그리고 @librechat/api dist (definitionsOnly 경로의 builtin tool registry).

const fs = require('fs');

const PATH = '/app/api/app/clients/tools/structured/StableDiffusion.js';
const HANDLE_PATH = '/app/api/app/clients/tools/util/handleTools.js';
const MANIFEST_PATH = '/app/api/app/clients/tools/manifest.json';
const DIST_PATH = '/app/packages/api/dist/index.js';
const MARKER = 'KLOUDCHAT_SD_MODEL_PATCH';
const NEW_TOOL_NAME = 'generate_image';

let src = fs.readFileSync(PATH, 'utf8');
if (src.includes(MARKER)) {
  console.log('[patch_librechat_sd_model] already applied, skipping');
  process.exit(0);
}

const SCHEMA_NEEDLE =
  "  required: ['prompt', 'negative_prompt'],\n};";
// enum 에 들어가는 alias 들: 로컬 ComfyUI (flux/qwen) + 외부 OR 경유 (nano-banana, gpt-image-2).
// shim 의 OR_IMAGE_MODELS 매핑이 nano-banana/gpt-image-2 를 LiteLLM 으로 분기. enum 에서 빠지면
// 모델이 부르려 해도 schema validator 가 거부 → fallback 으로 로컬 alias 잘못 사용.
// description 은 의도적으로 매력적인 변별 문구 (e.g. "Asian text 좋음") 를 빼고 중립화 — LLM 이
// 에이전트 instructions 무시하고 enum description 보고 다른 alias 픽하는 사고 방지. 어떤 모델을
// 써야 하는지는 agent instructions 에 strict 하게 박혀있음.
const SCHEMA_REPLACEMENT =
  "    model: {\n" +
  "      type: 'string',\n" +
  "      enum: ['flux-schnell', 'flux-dev', 'qwen-image', 'qwen-image-edit', 'nano-banana', 'gpt-image-2'],\n" +
  "      description: 'Image model alias. Use ONLY the value specified in your agent instructions — other entries are reserved for other agents and routing to them yields wrong-backend errors.',\n" +
  "    },\n" +
  "  },\n" +
  "  required: ['prompt', 'negative_prompt'],\n" +
  "}; // " + MARKER;

if (!src.includes(SCHEMA_NEEDLE)) {
  console.error('[patch_librechat_sd_model] schema NEEDLE not found — LibreChat upstream changed?');
  process.exit(1);
}
src = src.replace(
  "    },\n  },\n  required: ['prompt', 'negative_prompt'],\n};",
  "    },\n" + SCHEMA_REPLACEMENT
);

const PAYLOAD_NEEDLE =
  "    const { prompt, negative_prompt } = data;\n" +
  "    const payload = {\n" +
  "      prompt,\n" +
  "      negative_prompt,\n" +
  "      cfg_scale: 4.5,\n" +
  "      steps: 22,\n" +
  "      width: 1024,\n" +
  "      height: 1024,\n" +
  "    };";
const PAYLOAD_REPLACEMENT =
  "    const { prompt, negative_prompt, model } = data;\n" +
  "    const payload = {\n" +
  "      prompt,\n" +
  "      negative_prompt,\n" +
  "      cfg_scale: 4.5,\n" +
  "      steps: 22,\n" +
  "      width: 1024,\n" +
  "      height: 1024,\n" +
  "      ...(model ? { override_settings: { sd_model_checkpoint: model } } : {}),\n" +
  "    };";

if (!src.includes(PAYLOAD_NEEDLE)) {
  console.error('[patch_librechat_sd_model] payload NEEDLE not found — LibreChat upstream changed?');
  process.exit(1);
}
src = src.replace(PAYLOAD_NEEDLE, PAYLOAD_REPLACEMENT);

// ── 3. 툴 이름 rename: 'stable-diffusion' → 'generate_image'
src = src.replace("this.name = 'stable-diffusion';", "this.name = '" + NEW_TOOL_NAME + "';");
src = src.replace(
  "You can generate images using text with 'stable-diffusion'.",
  "You can generate images using text with '" + NEW_TOOL_NAME + "'."
);

fs.writeFileSync(PATH, src);

// handleTools.js — router + imageGenOptions 매핑 두 군데
let h = fs.readFileSync(HANDLE_PATH, 'utf8');
h = h.replace(/'stable-diffusion'/g, "'" + NEW_TOOL_NAME + "'");
fs.writeFileSync(HANDLE_PATH, h);

// manifest.json — pluginKey + 사용자 표시 name + authConfig 비우기.
// authConfig 가 있으면 LibreChat 가 사용자별 PluginAuth doc (mongo 의 pluginauths) 에서
// SD_WEBUI_URL 을 강제로 찾고, 없으면 loadAuthValues 가 throw → tool 이 silent skip 됨.
// SD_WEBUI_URL 은 docker-compose env 로 이미 LibreChat 컨테이너에 주입돼 있고
// StructuredSD.getServerURL() 가 process.env.SD_WEBUI_URL 로 fallback 하므로 authConfig 불필요.
const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
for (const tool of manifest) {
  if (tool.pluginKey === 'stable-diffusion' || tool.pluginKey === NEW_TOOL_NAME) {
    if (tool.name === 'Stable Diffusion') tool.name = 'Image Generation';
    tool.pluginKey = NEW_TOOL_NAME;
    tool.authConfig = [];
  }
}
fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2));

// @librechat/api dist — builtin tool registry rename.
// `loadToolDefinitions` (definitionsOnly 경로 — agents 가 LLM 한테 보낼 tool definitions 를
// 만들 때 사용) 가 `getToolDefinition(toolName)` 으로 dist 의 toolDefinitions 객체에서
// entry 를 찾는데, dist 는 'stable-diffusion' 키로만 등록돼 있어 'generate_image' 호출
// 시 silent skip → LLM tool array 에서 누락. 이 패치가 dist 를 'generate_image' 키로
// rename 해서 매칭되게 한다.
const distSrc = fs.readFileSync(DIST_PATH, 'utf8');
let distOut = distSrc;
const distReplacements = [
  ["'stable-diffusion':", "'generate_image':"],
  ["name: 'stable-diffusion',", "name: 'generate_image',"],
  ["with 'stable-diffusion'", "with '" + NEW_TOOL_NAME + "'"],
];
let distChanged = 0;
for (const [from, to] of distReplacements) {
  if (distOut.includes(from)) {
    distOut = distOut.split(from).join(to);
    distChanged++;
  }
}
if (distChanged === 0 && !distSrc.includes("'generate_image':")) {
  console.error('[patch_librechat_sd_model] dist NEEDLE not found — @librechat/api upstream changed?');
  process.exit(1);
}
fs.writeFileSync(DIST_PATH, distOut);

console.log('[patch_librechat_sd_model] applied schema + payload + rename patches (incl. dist registry: ' + distChanged + ' replacements)');
