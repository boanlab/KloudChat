"""
rag_api 의 document_loader.py 에 HWP(.hwp) 분기 in-place 추가.

증상: rag_api base 이미지의 get_loader() 가 hwp 확장자를 처리 안 해서 마지막 else 의
TextLoader 로 fallback → 이진 OLE 데이터를 텍스트로 읽어 임베딩이 깨진 garbage →
한글(HWP) RAG 정확도 0.

해결: hwp5txt CLI 로 .hwp → .txt 변환 후 TextLoader 로 처리.
hwp5txt 는 base 이미지의 pyhwp 패키지에 entry_point 로 포함됨 (/usr/local/bin/hwp5txt).

Dockerfile.rag 의 빌드 단계에서 한 번만 실행. 멱등: 이미 patch 된 경우 skip.
base 이미지가 업그레이드되어 get_loader 함수가 바뀌어 패턴이 안 맞으면 명시적 실패.
"""

import re
import sys

PATH = "/app/app/utils/document_loader.py"

HWP_BRANCH = '''    elif file_ext == "hwp" or file_content_type in [
        "application/x-hwp",
        "application/vnd.hancom.hwp",
        "application/haansofthwp",
        "application/haansoftxhwpml",
    ]:
        import subprocess, tempfile, os
        txt_fd, txt_path = tempfile.mkstemp(suffix=".txt")
        os.close(txt_fd)
        try:
            subprocess.run(
                ["hwp5txt", filepath, "--output", txt_path],
                check=True, capture_output=True, timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            try:
                os.unlink(txt_path)
            except OSError:
                pass
            raise RuntimeError(
                f"hwp5txt failed for {filename}: {getattr(e, 'stderr', e)}"
            ) from e
        loader = TextLoader(txt_path, autodetect_encoding=True)
        loader._temp_filepath = txt_path
'''

with open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

if 'file_ext == "hwp"' in src:
    print("[patch_document_loader] HWP branch already present, skipping")
    sys.exit(0)

pattern = (
    r"    else:\n"
    r"        loader = TextLoader\(filepath, autodetect_encoding=True\)\n"
    r"        known_type = False"
)
m = re.search(pattern, src)
if not m:
    print("[patch_document_loader] PATTERN NOT FOUND — base image's get_loader changed. Manual review needed.")
    sys.exit(1)

new_src = src[: m.start()] + HWP_BRANCH + src[m.start() :]
with open(PATH, "w", encoding="utf-8") as f:
    f.write(new_src)
print(f"[patch_document_loader] HWP branch inserted at offset {m.start()}")
