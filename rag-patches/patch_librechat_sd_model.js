// LibreChat stable-diffusion 툴 → 'generate_image' rename + 스키마에 'model' enum
// 필드와 override_settings 주입. 영향: StableDiffusion.js, handleTools.js, manifest.json,
// + @librechat/api dist (definitionsOnly 경로의 builtin tool registry).

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
// enum visible only to local/super agents (manage.sh::builtinFor drops the
// tool for external providers).
const SCHEMA_REPLACEMENT =
  "    model: {\n" +
  "      type: 'string',\n" +
  "      enum: ['flux-schnell', 'flux-dev', 'nano-banana', 'nano-banana-2', 'gpt-image-2'],\n" +
  "      description: 'Image model alias. DEFAULT = flux-schnell (local, free, ~90s, 4 steps). flux-dev = local high-quality (~2-3 min, free). External paid models: nano-banana (Gemini 2.5 Flash Image, ~5-10s, ~$0.04/img, fast iteration + multi-image editing), nano-banana-2 (Gemini 3.1 Flash Image Preview, Pro-quality at Flash speed, ~$0.05/img), gpt-image-2 (OpenAI GPT-5.4 Image 2, ~$0.10-0.30/img, best for text-in-image / posters / UI / diagrams). Use external models ONLY when the user explicitly names them or requests capabilities local cannot satisfy (e.g. text rendering).',\n" +
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
// `user` 주입 = 외부 image-gen (nano-banana 등) spend 를 호출자에게 귀속하기 위함.
// comfyui-shim 이 LiteLLM 의 chat/completions 로 넘길 때 extra_body.user 로 전달 →
// LiteLLM 이 end_user 컬럼에 기록 → mcp/usage.py 의 /customer/daily/activity?
// end_user_ids= 필터가 포착. 로컬 ComfyUI 경로는 user 필드 무시 (워크플로 영향
// 없음). req.user.email = LibreChat 의 Express user 객체에서 유래 — 없으면 Mongo
// userId 로 폴백.
const PAYLOAD_REPLACEMENT =
  "    const { prompt, negative_prompt, model } = data;\n" +
  "    const callerUser = (this.req && this.req.user && this.req.user.email) || this.userId || '';\n" +
  "    const payload = {\n" +
  "      prompt,\n" +
  "      negative_prompt,\n" +
  "      cfg_scale: 4.5,\n" +
  "      steps: 22,\n" +
  "      width: 1024,\n" +
  "      height: 1024,\n" +
  "      ...(callerUser ? { user: callerUser } : {}),\n" +
  "      ...(model ? { override_settings: { sd_model_checkpoint: model } } : {}),\n" +
  "    };";

if (!src.includes(PAYLOAD_NEEDLE)) {
  console.error('[patch_librechat_sd_model] payload NEEDLE not found — LibreChat upstream changed?');
  process.exit(1);
}
src = src.replace(PAYLOAD_NEEDLE, PAYLOAD_REPLACEMENT);

// 툴 이름 rename: 'stable-diffusion' → 'generate_image'.
src = src.replace("this.name = 'stable-diffusion';", "this.name = '" + NEW_TOOL_NAME + "';");
src = src.replace(
  "You can generate images using text with 'stable-diffusion'.",
  "You can generate images using text with '" + NEW_TOOL_NAME + "'."
);

fs.writeFileSync(PATH, src);

// handleTools.js — tool name rename ('stable-diffusion' → 'generate_image') 두 곳:
// (1) toolConstructors 의 key, (2) toolOptions 의 key.
let h = fs.readFileSync(HANDLE_PATH, 'utf8');
h = h.replace(/'stable-diffusion'/g, "'" + NEW_TOOL_NAME + "'");
fs.writeFileSync(HANDLE_PATH, h);

// manifest.json — rename pluginKey + display name; clear authConfig so
// LibreChat skips the per-user PluginAuth lookup and falls back to
// process.env.SD_WEBUI_URL (.env 의 값 — LibreChat 가 마운트된 .env 에서 읽음).
const manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'));
for (const tool of manifest) {
  if (tool.pluginKey === 'stable-diffusion' || tool.pluginKey === NEW_TOOL_NAME) {
    if (tool.name === 'Stable Diffusion') tool.name = 'Image Generation';
    tool.pluginKey = NEW_TOOL_NAME;
    tool.authConfig = [];
  }
}
fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2));

// @librechat/api dist registry — rename the 'stable-diffusion' key to
// 'generate_image' so loadToolDefinitions() finds the entry under the new
// name (definitionsOnly path used to build LLM-visible tool list).
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

// Inject the model enum into dist's stableDiffusionSchema — that copy is
// what agents see, not the live StableDiffusion.js.
const SCHEMA_DIST_NEEDLE =
  "const stableDiffusionSchema = {\n" +
  "    type: 'object',\n" +
  "    properties: {\n" +
  "        prompt: {\n" +
  "            type: 'string',\n" +
  "            description: 'Detailed keywords to describe the subject, using at least 7 keywords to accurately describe the image, separated by comma',\n" +
  "        },\n" +
  "        negative_prompt: {\n" +
  "            type: 'string',\n" +
  "            description: 'Keywords we want to exclude from the final image, using at least 7 keywords to accurately describe the image, separated by comma',\n" +
  "        },\n" +
  "    },\n" +
  "    required: ['prompt', 'negative_prompt'],\n" +
  "};";
const SCHEMA_DIST_REPLACEMENT =
  "const stableDiffusionSchema = {\n" +
  "    type: 'object',\n" +
  "    properties: {\n" +
  "        prompt: {\n" +
  "            type: 'string',\n" +
  "            description: 'Detailed keywords to describe the subject, using at least 7 keywords to accurately describe the image, separated by comma',\n" +
  "        },\n" +
  "        negative_prompt: {\n" +
  "            type: 'string',\n" +
  "            description: 'Keywords we want to exclude from the final image, using at least 7 keywords to accurately describe the image, separated by comma',\n" +
  "        },\n" +
  "        model: {\n" +
  "            type: 'string',\n" +
  "            enum: ['flux-schnell', 'flux-dev', 'nano-banana', 'nano-banana-2', 'gpt-image-2'],\n" +
  "            description: 'Image model alias. DEFAULT = flux-schnell (local, free, ~90s, 4 steps). flux-dev = local high-quality (~2-3 min, free). External paid models: nano-banana (Gemini 2.5 Flash Image, ~5-10s, ~$0.04/img, fast iteration + multi-image editing), nano-banana-2 (Gemini 3.1 Flash Image Preview, Pro-quality at Flash speed, ~$0.05/img), gpt-image-2 (OpenAI GPT-5.4 Image 2, ~$0.10-0.30/img, best for text-in-image / posters / UI / diagrams). Use external models ONLY when the user explicitly names them or requests capabilities local cannot satisfy (e.g. text rendering).',\n" +
  "        },\n" +
  "    },\n" +
  "    required: ['prompt', 'negative_prompt'],\n" +
  "};";
if (distOut.includes(SCHEMA_DIST_NEEDLE)) {
  distOut = distOut.replace(SCHEMA_DIST_NEEDLE, SCHEMA_DIST_REPLACEMENT);
  distChanged++;
}

fs.writeFileSync(DIST_PATH, distOut);

console.log('[patch_librechat_sd_model] applied schema + payload + rename patches (incl. dist registry: ' + distChanged + ' replacements)');
