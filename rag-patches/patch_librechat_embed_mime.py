"""LibreChat 의 RAG 업로드(uploadVectors) 가 rag_api /embed 로 파일을 보낼 때
원본 파일명과 MIME 타입을 명시하도록 패치.

문제:
  base 의 VectorDB/crud.js 는 `formData.append('file', fs.createReadStream(file.path))`
  로만 보낸다. file.path 는 multer 임시 경로(확장자 없는 해시) 라 multipart 의 filename
  에 확장자가 없고, content-type 은 form-data 기본값 application/octet-stream 이 된다.
  rag_api 의 get_loader(filename, content_type, ...) 는 확장자도 MIME 도 못 맞춰
  HWP(및 기타 확장자 의존 포맷) 분기를 놓치고 TextLoader 로 fallback → OLE 이진을
  그대로 임베딩해 검색 품질이 깨진다 (embedded 는 되지만 내용이 쓰레기).

수정:
  3번째 인자로 {filename: file.originalname, contentType: file.mimetype} 을 넘겨
  rag_api 가 확장자/ MIME 으로 올바른 로더(HWP→hwp5txt 등) 를 선택하게 한다.

Dockerfile.librechat 빌드 단계에서 1회 실행. 멱등(이미 패치면 skip).
base 구조가 바뀌어 패턴이 안 맞으면 명시적 실패.
"""

import sys

PATH = "/app/api/server/services/Files/VectorDB/crud.js"
NEEDLE = "formData.append('file', fs.createReadStream(file.path));"
REPLACEMENT = (
    "formData.append('file', fs.createReadStream(file.path), {\n"
    "      filename: file.originalname,\n"
    "      contentType: file.mimetype,\n"
    "    });"
)

with open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

if "filename: file.originalname" in src and "contentType: file.mimetype" in src:
    print("[patch_librechat_embed_mime] already patched, skipping")
    sys.exit(0)

if NEEDLE not in src:
    print(f"[patch_librechat_embed_mime] PATTERN NOT FOUND in {PATH} — base structure changed")
    sys.exit(1)

count = src.count(NEEDLE)
if count != 1:
    print(f"[patch_librechat_embed_mime] expected 1 occurrence, found {count} — aborting")
    sys.exit(1)

new_src = src.replace(NEEDLE, REPLACEMENT)
with open(PATH, "w", encoding="utf-8") as f:
    f.write(new_src)
print("[patch_librechat_embed_mime] embed filename/contentType injected")
