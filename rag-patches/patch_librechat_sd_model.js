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
// enum: 로컬 ComfyUI alias 만. 외부 OR 경유 image (nano-banana / gpt-image-2) 는 응답 안정성
// 문제로 비활성 — generate_image tool 은 ollama 기반 에이전트에서만 의미. 외부 provider
// 에이전트는 builtinFor 가 자동으로 tool 제외 (manage.sh EXT_IMAGE_FOR_PROVIDER 비어있음).
const SCHEMA_REPLACEMENT =
  "    model: {\n" +
  "      type: 'string',\n" +
  "      enum: ['flux-schnell', 'flux-dev', 'qwen-image', 'qwen-image-edit'],\n" +
  "      description: 'Image model alias. flux-schnell = fast draft (4 steps), flux-dev = high quality, qwen-image = text-in-image / detailed composition, qwen-image-edit = img2img edit.',\n" +
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

// ── 4. Server-side model override: agent.model 의 provider prefix 보고 data.model 강제 교체.
// LLM 이 enum 의 잘못된 alias (qwen-image 등) 를 픽해도 _call 에서 정정. instructions/description
// 만으로는 LLM 협조 의존이라 가끔 drift — 서버 측 enforce 가 신뢰성 있다.
//
// (a) constructor 에 this.agentModel 캡처:
src = src.replace(
  "this.isAgent = fields.isAgent;",
  "this.isAgent = fields.isAgent;\n    /** @type {string|undefined} agent.model — provider 별 image alias 강제 override 용 (KLOUDCHAT) */\n    this.agentModel = fields.agentModel;"
);

// (b) _call 진입부에 override 블록 삽입:
const CALL_NEEDLE = "  async _call(data) {\n    const url = this.url;\n    const { prompt, negative_prompt, model } = data;";
const CALL_REPLACEMENT =
  "  async _call(data) {\n" +
  "    // KLOUDCHAT_SD_MODEL_PATCH — 외부 provider (openai/anthropic/google) 에이전트가\n" +
  "    // generate_image 호출하면 즉시 거부. 외부 OR image 모델 (gpt-5.4-image-2 /\n" +
  "    // gemini-3-pro-image-preview) 응답이 OR proxy 측에서 hang/깨짐 — local ComfyUI\n" +
  "    // 라우팅은 의도와 안 맞음. ollama 에이전트만 이 tool 사용. agentModel 없으면 legacy skip.\n" +
  "    if (this.agentModel) {\n" +
  "      const __prov = String(this.agentModel).split('/')[0];\n" +
  "      if (__prov !== 'ollama') {\n" +
  "        return this.returnValue('Image generation is currently wired only for ollama-based agents. Switch to an ollama agent (qwen3.6:35b / qwen3-coder-next 등) for image generation.');\n" +
  "      }\n" +
  "    }\n" +
  "    const url = this.url;\n" +
  "    const { prompt, negative_prompt, model } = data;";

if (!src.includes(CALL_NEEDLE)) {
  console.error('[patch_librechat_sd_model] _call NEEDLE not found — LibreChat upstream changed?');
  process.exit(1);
}
src = src.replace(CALL_NEEDLE, CALL_REPLACEMENT);

fs.writeFileSync(PATH, src);

// handleTools.js — (1) tool name rename, (2) imageGenOptions 에 agent.model 주입.
let h = fs.readFileSync(HANDLE_PATH, 'utf8');
h = h.replace(/'stable-diffusion'/g, "'" + NEW_TOOL_NAME + "'");

const IMG_OPT_NEEDLE = "const imageGenOptions = {\n    isAgent: !!agent,\n    req: options.req,\n    fileStrategy,\n    processFileURL: options.processFileURL,\n    returnMetadata: options.returnMetadata,\n    uploadImageBuffer: options.uploadImageBuffer,\n  };";
const IMG_OPT_REPLACEMENT =
  "const imageGenOptions = {\n" +
  "    isAgent: !!agent,\n" +
  "    req: options.req,\n" +
  "    fileStrategy,\n" +
  "    processFileURL: options.processFileURL,\n" +
  "    returnMetadata: options.returnMetadata,\n" +
  "    uploadImageBuffer: options.uploadImageBuffer,\n" +
  "    agentModel: agent && agent.model,  // KLOUDCHAT_SD_MODEL_PATCH — server-side image alias override\n" +
  "  };";
if (!h.includes(IMG_OPT_NEEDLE)) {
  console.error('[patch_librechat_sd_model] imageGenOptions NEEDLE not found in handleTools.js');
  process.exit(1);
}
h = h.replace(IMG_OPT_NEEDLE, IMG_OPT_REPLACEMENT);
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
