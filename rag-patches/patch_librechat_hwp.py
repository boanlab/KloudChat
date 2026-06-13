"""
LibreChat client 소스 (data-provider/src/file-config.ts) inferMimeType 함수에
.hwp / .hwpx 분기 추가. Dockerfile.librechat multi-stage builder 단계 실행.

base 코드:
    const extension = fileName.split('.').pop()?.toLowerCase() ?? '';
    return codeTypeMapping[extension] || imageTypeMapping[extension] || currentType;

패치 후:
    const extension = fileName.split('.').pop()?.toLowerCase() ?? '';
    if (extension === 'hwp' || extension === 'hwpx') return 'application/x-hwp';
    return codeTypeMapping[extension] || imageTypeMapping[extension] || currentType;

vite build 시 새 chunk hash 자동 생성 → 캐시·service worker 충돌 없음.
"""

import sys

PATH = "packages/data-provider/src/file-config.ts"
NEEDLE = "return codeTypeMapping[extension] || imageTypeMapping[extension] || currentType;"
ADDITION = "if (extension === 'hwp' || extension === 'hwpx') return 'application/x-hwp';\n  "

with open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

if "extension === 'hwp'" in src:
    print("[patch_librechat_hwp] HWP branch already present, skipping")
    sys.exit(0)

if NEEDLE not in src:
    print(f"[patch_librechat_hwp] PATTERN NOT FOUND in {PATH} — LibreChat source structure changed")
    sys.exit(1)

new_src = src.replace(NEEDLE, ADDITION + NEEDLE)
with open(PATH, "w", encoding="utf-8") as f:
    f.write(new_src)
print(f"[patch_librechat_hwp] HWP branch inserted in {PATH}")
