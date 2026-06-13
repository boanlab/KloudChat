# slide-export — HTML 덱 → PDF 렌더 사이드카 (headless chromium).
# python-slim + chromium 런타임 libs(명시) + playwright chromium 바이너리.
# (`playwright install --with-deps` 는 bookworm 에 없는 ttf-unifont 등에서 실패 → libs 직접.)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
      libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
      fonts-liberation fonts-noto-cjk fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "playwright==1.49.0" && playwright install chromium
RUN pip install --no-cache-dir \
      "fastapi==0.115.6" "uvicorn[standard]==0.32.1" "python-pptx==1.0.2" "pillow==11.0.0"

WORKDIR /app
COPY app.py .

EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
