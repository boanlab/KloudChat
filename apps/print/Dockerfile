# The printer.
#
# One job: turn a finished HTML document into a PDF that looks like the screen.
#
# It is a container of its own rather than a few lines in the API image for
# three reasons, in order of weight:
#
#   · What it opens is written by a model. Sanitised, script-free, and still
#     the least trusted input this product handles. Here it is opened in a
#     process with no database, no secrets, and — see main.py — no network.
#   · A browser is ~740 MB. The API image is 585 MB and is rebuilt on every
#     change to a Python file; doubling it would be paid for on every push.
#   · The stack already keeps its heavy renderers beside it (code-interpreter,
#     crawl4ai). This reads the same way.
#
# `chromium-headless-shell` rather than full Chromium: it is the build meant
# for exactly this — printing and screenshots, no window, no UI — and it is
# about half the size. The library list below is what `ldd` reports the shell
# actually links against, rather than `playwright install --with-deps`, which
# installs the dependency set for a full desktop Chromium and costs ~130 MB
# more for packages nothing here loads.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# fonts-nanum — the document seeds ask for a Korean face and fall through to
# whatever fontconfig finds. Without a Korean font installed every Hangul
# glyph prints as a box, and the failure is silent: the PDF is the right
# length and says nothing.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates fonts-nanum \
      libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
      libxcb1 libxkbcommon0 libexpat1 libdbus-1-3 libglib2.0-0 libnss3 \
      libnspr4 libatk1.0-0 libatk-bridge2.0-0 libatspi2.0-0 libasound2 libgbm1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
      "fastapi==0.115.6" "uvicorn[standard]==0.34.0" "playwright==1.55.0" \
    && playwright install chromium-headless-shell \
    && rm -rf /opt/playwright/ffmpeg-*

COPY main.py ./

RUN useradd --uid 1000 --create-home printer
USER printer

EXPOSE 8200
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8200"]
