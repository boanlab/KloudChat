# The printer: finished HTML in, PDF out.
#
# A container of its own: it opens model-authored markup, so it runs with no
# database, no secrets and (see main.py) no network; and a browser is ~740 MB
# the API image should not carry.
#
# `chromium-headless-shell` is the printing/screenshot build, about half the
# size of full Chromium. The library list is what `ldd` reports the shell
# links against; `playwright install --with-deps` would add ~130 MB for a
# desktop Chromium.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# fonts-nanum: without a Korean face every Hangul glyph prints as a box.
# libreoffice-impress-nogui + poppler-utils: figure decks (python-pptx) to PNG (/pptx/png).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates fonts-nanum curl unzip \
      libreoffice-impress-nogui poppler-utils \
      libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
      libxcb1 libxkbcommon0 libexpat1 libdbus-1-3 libglib2.0-0 libnss3 \
      libnspr4 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libasound2 libgbm1 \
    && rm -rf /var/lib/apt/lists/*

# Pretendard, the app's face, so a figure renders as the deck file and the web view do.
# Pinned release (SIL Open Font License 1.1); fetched at build, never at run.
ARG PRETENDARD_VERSION=1.3.9
RUN curl -fsSL -o /tmp/pretendard.zip \
      "https://github.com/orioncactus/pretendard/releases/download/v${PRETENDARD_VERSION}/Pretendard-${PRETENDARD_VERSION}.zip" \
    && mkdir -p /usr/share/fonts/opentype/pretendard \
    && unzip -j -o /tmp/pretendard.zip "public/static/Pretendard-*.otf" -d /usr/share/fonts/opentype/pretendard \
    && rm /tmp/pretendard.zip && fc-cache -f >/dev/null

RUN pip install --no-cache-dir \
      "fastapi==0.115.6" "uvicorn[standard]==0.34.0" "playwright==1.55.0" \
    && playwright install chromium-headless-shell \
    && rm -rf /opt/playwright/ffmpeg-*

COPY main.py ./

RUN useradd --uid 1000 --create-home printer
USER printer

EXPOSE 8200
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8200"]
