"""LibreChat RAG 업로드(uploadVectors) → rag_api /embed 전송 시 원본 파일명·MIME
타입 명시 패치.

문제:
  base VectorDB/crud.js = `formData.append('file', fs.createReadStream(file.path))`
  로만 전송. file.path = multer 임시 경로(확장자 없는 해시) → multipart filename 에
  확장자 없음, content-type = form-data 기본값 application/octet-stream.
  rag_api get_loader(filename, content_type, ...) 가 확장자·MIME 둘 다 못 맞춰
  HWP(및 기타 확장자 의존 포맷) 분기 누락 → TextLoader fallback → OLE 이진 그대로
  임베딩 → 검색 품질 깨짐 (embedded 는 되지만 내용 쓰레기).

수정:
  3번째 인자 {filename: file.originalname, contentType: file.mimetype} 전달 →
  rag_api 가 확장자·MIME 으로 올바른 로더(HWP→hwp5txt 등) 선택.

Dockerfile.librechat 빌드 단계 1회 실행. 멱등(이미 패치면 skip).
base 구조 변경으로 패턴 불일치 시 명시적 실패.
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
