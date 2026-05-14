// LibreChat 의 stable-diffusion 툴을 'image-generation' 으로 rename + 스키마에 'model' enum
// 필드와 override_settings 주입. 영향: StableDiffusion.js, handleTools.js, manifest.json.

const fs = require('fs');

const PATH = '/app/api/app/clients/tools/structured/StableDiffusion.js';
const HANDLE_PATH = '/app/api/app/clients/tools/util/handleTools.js';
const MANIFEST_PATH = '/app/api/app/clients/tools/manifest.json';
const MARKER = 'KLOUDCHAT_SD_MODEL_PATCH';
const NEW_TOOL_NAME = 'image-generation';

let src = fs.readFileSync(PATH, 'utf8');
if (src.includes(MARKER)) {
  console.log('[patch_librechat_sd_model] already applied, skipping');
  process.exit(0);
}

const SCHEMA_NEEDLE =
  "  required: ['prompt', 'negative_prompt'],\n};";
const SCHEMA_REPLACEMENT =
  "    model: {\n" +
  "      type: 'string',\n" +
  "      enum: ['sdxl', 'qwen-image', 'qwen-image-edit', 'flux-dev', 'flux-schnell'],\n" +
  "      description: 'Image base model. sdxl=fast SDXL, qwen-image=Qwen-Image GGUF, qwen-image-edit=img2img edit, flux-dev=high quality (slow), flux-schnell=fast Flux (4 steps).',\n" +
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

// ── 3. 툴 이름 rename: 'stable-diffusion' → 'image-generation'
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

// manifest.json — pluginKey + 사용자 표시 name
let m = fs.readFileSync(MANIFEST_PATH, 'utf8');
m = m.replace('"name": "Stable Diffusion"', '"name": "Image Generation"');
m = m.replace('"pluginKey": "stable-diffusion"', '"pluginKey": "' + NEW_TOOL_NAME + '"');
fs.writeFileSync(MANIFEST_PATH, m);

console.log('[patch_librechat_sd_model] applied schema + payload + rename patches');
