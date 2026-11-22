# KloudChat media worker

Stock `ffmpeg` / `ffprobe` behind a small HTTP API, so the API can understand uploaded
audio and video. ffmpeg runs only as Debian's unmodified program in this container; nothing
links it (see [NOTICE](../../NOTICE)). The API asks for one step at a time (probe, cut an
audio piece for speech-to-text, pick scene-change frames, extract a frame) and does the rest
itself. The server is the Python standard library only.

The container joins an internal network with no route out, holds no secrets, mounts the
API's file storage read-only and writes only to a tmpfs. With `MEDIA_URL` blank on the API,
audio and video are transcribed whole at upload and no frames are read.

## Running

It starts with the stack (`docker compose up -d`; image `boanlab/kloudchat-media`, or
`docker-compose.build.yml` to build it) and listens on 8300 inside the `media` network only.
The API reaches it at `KCHAT_MEDIA_URL` (default `http://kloudchat-media:8300`).

```bash
docker compose logs -f media
```

## Endpoints

`key` is a storage key under `MEDIA_ROOT`; a key that leaves it answers 400, a missing file 404.

| Endpoint | Body | Answer |
| --- | --- | --- |
| `GET /health` | | 200 |
| `POST /probe` | `{key}` | duration, start, `hasVideo`, `hasAudio`, size, codecs, format, `maxSeconds` |
| `POST /audio` | `{key, start, duration}` | `audio/flac`, 16 kHz mono, at most 30 minutes |
| `POST /scenes` | `{key, threshold, min_gap, max_gap, max_frames, fps}` | `{frames: [{t, score}], seconds, mode}`; 413 past `MEDIA_MAX_SECONDS` |
| `POST /frame` | `{key, t, width}` | `image/jpeg` |

Errors are `{"detail": "<reason>"}`: 422 for a file ffmpeg cannot read, 504 for a step past
`MEDIA_STEP_TIMEOUT`.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEDIA_PORT` | `8300` | Listen port |
| `MEDIA_ROOT` | `/srv/data/files` | The API's file storage, mounted read-only |
| `MEDIA_JOBS` | `2` | ffmpeg runs at once; more requests wait |
| `MEDIA_THREADS` | `2` | Decoder and encoder threads per run |
| `MEDIA_MAX_SECONDS` | `14400` | Longest video accepted, in seconds |
| `MEDIA_STEP_TIMEOUT` | `900` | One ffmpeg run's wall-clock ceiling, in seconds |
| `MEDIA_SCAN_PARALLEL` | `4` | Frames decoded side by side in a scene scan |
| `MEDIA_KEYFRAME_MIN_HEIGHT` · `MEDIA_KEYFRAME_MAX_GAP` | `900` · `6` | Keyframes replace a full decode for pictures at least this tall that have one at least every this many seconds |
| `MEDIA_TMP` | system temp | Scratch for audio pieces |
| `MEDIA_LOG_LEVEL` | `INFO` | Log level |

Compose sets `MEDIA_JOBS`, `MEDIA_THREADS` and `MEDIA_MAX_SECONDS` from `KCHAT_MEDIA_JOBS`,
`KCHAT_MEDIA_THREADS` and `KCHAT_MEDIA_MAX_SECONDS`, and caps the container with
`KCHAT_MEDIA_CPUS` (4) and `KCHAT_MEDIA_MEMORY` (2g).

## Tests

`tests/test_server.py` generates a 30-second clip with ffmpeg, so it runs inside the image:

```bash
docker build -t kloudchat-media:ci apps/media
docker run --rm -v "$PWD/apps/media:/t:ro" -w /t kloudchat-media:ci python -m unittest discover -s tests
```
