"""OpenAI-compatible whisper server (faster-whisper backend).

POST /v1/audio/transcriptions  — multipart file + form fields. 응답은 OpenAI 와 동일한
스키마: {"text": "..."}. `language` / `prompt` / `response_format` 받음.

Model 은 첫 호출 시 로드 후 메모리에 상주 (WhisperModel 인스턴스 lazy). WHISPER_DEVICE=auto
는 CUDA 가용 시 GPU, 아니면 CPU. WHISPER_COMPUTE_TYPE 은 float16 (GPU 기본) / int8 (CPU).
설정값을 GPU/ct2 가 미지원하면 로드 시 int8 로 자동 폴백한다.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from faster_whisper import WhisperModel

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
LOG = logging.getLogger("whisper")

MODEL_NAME    = os.getenv("WHISPER_MODEL", "large-v3")
DEVICE        = os.getenv("WHISPER_DEVICE", "auto")
COMPUTE_TYPE  = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
DOWNLOAD_ROOT = os.getenv("HF_HOME", "/var/lib/whisper")

_model: Optional[WhisperModel] = None
_model_lock = threading.Lock()


def _get_model() -> WhisperModel:
    """Lazy-load — 첫 호출 시 weight 다운로드 + GPU 로드 (이후 캐시).
    double-checked locking 으로 동시 첫 호출 시 중복 init 회피."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                LOG.info("Loading WhisperModel(%s, device=%s, compute_type=%s)",
                         MODEL_NAME, DEVICE, COMPUTE_TYPE)
                try:
                    _model = WhisperModel(
                        MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE,
                        download_root=DOWNLOAD_ROOT,
                    )
                except Exception as e:
                    # GPU/ct2 가 compute_type 미지원(예: 일부 카드의 float16/int8_float16) → int8 폴백.
                    if COMPUTE_TYPE == "int8":
                        raise
                    LOG.warning("compute_type=%s 로드 실패(%s) → int8 폴백", COMPUTE_TYPE, e)
                    _model = WhisperModel(
                        MODEL_NAME, device=DEVICE, compute_type="int8",
                        download_root=DOWNLOAD_ROOT,
                    )
    return _model


app = FastAPI(title="KloudChat Whisper", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL_NAME, "device": DEVICE}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),         # OpenAI 호환 필드 — 무시 (서버 single-model)
    language: Optional[str] = Form(None),
    prompt: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
    temperature: Optional[float] = Form(0.0),
) -> Response:
    # tempfile 로 떨어뜨려 ffmpeg 가 직접 읽게 — faster-whisper 가 stream API 없음.
    suffix = os.path.splitext(file.filename or "")[1] or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        m = _get_model()
        segments, info = m.transcribe(
            tmp_path,
            language=language,
            initial_prompt=prompt,
            temperature=temperature or 0.0,
            vad_filter=True,
        )
        text = "".join(seg.text for seg in segments).strip()
    except Exception as e:
        LOG.exception("transcribe failed")
        raise HTTPException(500, f"transcription failed: {e}")
    finally:
        try: os.unlink(tmp_path)
        except OSError: pass

    if response_format == "text":
        return PlainTextResponse(text)
    return JSONResponse({"text": text, "language": info.language})
