# The media worker: stock ffmpeg/ffprobe behind a small HTTP API (server.py).
#
# ffmpeg is LGPL/GPL. It is installed here as Debian's unmodified package and run only as a
# separate program; no KloudChat code links or bundles it. Its licence texts ship with the
# package under /usr/share/doc/ffmpeg* in this image (see NOTICE). The service holds no
# secrets, joins only an internal network and reads uploads through a read-only mount.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY server.py ./

RUN useradd --uid 1000 --create-home media
USER media

EXPOSE 8300
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8300/health', timeout=8).status == 200 else 1)"]
CMD ["python", "server.py"]
