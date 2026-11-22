# KloudChat print service

A headless-browser worker the API calls for three jobs: finished HTML to PDF, the first
slide of a `.pptx` to PNG, and layered layout of figure graphs. It opens model-authored
markup, so it is isolated: no published port, no database, no secrets, and a network with no
route out.

**Nothing in it reaches the network.** Every request the browser makes is aborted except the
blank page it starts on; documents embed their pictures as `data:` URIs.

## Running

It starts with the stack (`docker compose up -d`; image `boanlab/kloudchat-print`, or
`docker-compose.build.yml` to build it) and listens on 8200 inside the `printer` network only.
The API finds it through `KCHAT_PRINT_BASE_URL` (default `http://kloudchat-print:8200`). With
that blank, PDF exports still work, drawn by the API's structural renderer without the
template's own layout.

The image carries Chromium (Playwright's headless shell), LibreOffice Impress, poppler, elkjs
and the fonts documents use (Nanum, Pretendard, Selawik, Gelasio). Compose runs it with
`init: true` so LibreOffice's orphaned children are reaped.

## Endpoints

| Endpoint | Body | Answer |
| --- | --- | --- |
| `GET /health` | | `{ok, zombies}`; starts a browser page, and answers 503 when more than 200 defunct processes pile up |
| `POST /pdf` | `{html}` (at most 32 MB) | `application/pdf`; the page's `@page` rule sets the paper; 504 `print_timeout` after 30 s |
| `POST /layout` | `{graphs: [ELK graph, …]}` (1–16, each at most 256 KB of JSON) | `{graphs: […]}` with positions, sizes and edge routes, in the order sent; 413 `layout_too_large`, 422 `layout_failed` |
| `POST /pptx/png` | `{pptx, dpi}`: the `.pptx` base64-encoded (at most 8 MB), `dpi` 72–300 (default 192) | `image/png` of the first slide, through LibreOffice and poppler; 422 `bad_pptx` / `render_failed`, 503 `render_unavailable`, 504 `render_timeout` |

LibreOffice runs one conversion at a time. A slide goes through an intermediate `.odp` whose
Asian–Latin autospacing is switched off, so Korean and Latin text keep the spacing the
`.pptx` states.

## Tests

`tests/test_runner.py` covers the subprocess runner and the health signal with stand-in
programs, and runs inside the image:

```bash
docker build -t kloudchat-print:ci apps/print
docker run --rm -v "$PWD/apps/print:/t:ro" -w /t kloudchat-print:ci python -m unittest discover -s tests
```
