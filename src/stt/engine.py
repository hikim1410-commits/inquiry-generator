# -*- coding: utf-8 -*-
"""faster-whisper 전사 엔진 — CPU int8 + 내장 VAD.

무거운 의존성(faster_whisper)은 함수 안에서만 import 한다.
반환 세그먼트의 speaker 는 빈 문자열 — 화자 부여는 diarize.assign 이 한다.
"""
import os

from src.logutil import log as _log

DEFAULT_MODEL = "medium"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE = "int8"

ERR_NO_LIB = "음성 전사 라이브러리(faster-whisper)가 설치되어 있지 않습니다."
ERR_NO_MODEL = "음성 전사 모델이 없습니다. 인터넷에 연결한 뒤 다시 시도하면 모델을 내려받습니다."
ERR_CANCEL = "사용자가 취소했습니다."
ERR_NOT_FOUND = "파일을 찾을 수 없습니다."
ERR_TRANSCRIBE = "음성 전사에 실패했습니다."

_MODEL_EXC_NAMES = {
    "LocalEntryNotFoundError",
    "EntryNotFoundError",
    "RepositoryNotFoundError",
    "HfHubHTTPError",
    "OfflineModeIsEnabled",
    "GatedRepoError",
}
_MODEL_MSG_KEYS = (
    "huggingface",
    "hf_hub",
    "snapshot",
    "local_files_only",
    "cached snapshot",
    "download",
    "model.bin",
    "ctranslate2 model",
    "unable to open file",
)


def _import_whisper_model():
    """지연 import. 미설치·DLL 오류면 None."""
    try:
        from faster_whisper import WhisperModel
        return WhisperModel
    except Exception:
        return None


def _fail(error, duration_sec=0.0, language=""):
    return {
        "ok": False,
        "segments": [],
        "language": language or "",
        "duration_sec": float(duration_sec or 0.0),
        "error": error,
    }


def _ok(segments, language, duration_sec):
    return {
        "ok": True,
        "segments": segments,
        "language": language or "",
        "duration_sec": float(duration_sec or 0.0),
        "error": "",
    }


def _cancelled(should_cancel):
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:
        return False


def _report(on_progress, done_sec, total_sec):
    if on_progress is None:
        return
    try:
        on_progress(float(done_sec), float(total_sec))
    except Exception:
        pass


def _is_model_missing(exc):
    """모델 파일 부재·다운로드 실패를 라이브러리 미설치와 구분한다."""
    if type(exc).__name__ in _MODEL_EXC_NAMES:
        return True
    msg = str(exc).lower()
    return any(k in msg for k in _MODEL_MSG_KEYS)


def _merged_opts(opts):
    model = DEFAULT_MODEL
    language = ""
    compute_type = DEFAULT_COMPUTE
    device = DEFAULT_DEVICE
    if isinstance(opts, dict):
        if opts.get("model"):
            model = opts["model"]
        if opts.get("language") is not None:
            language = opts["language"]
        if opts.get("compute_type"):
            compute_type = opts["compute_type"]
        if opts.get("device"):
            device = opts["device"]
    return {
        "model": model,
        "language": language or "",
        "compute_type": compute_type,
        "device": device,
    }


def _as_segment(item):
    """faster-whisper Segment (또는 동형 객체/dict) → 계약 Segment. 실패하면 None."""
    if isinstance(item, dict):
        start, end = item.get("start"), item.get("end")
        text = item.get("text", "")
    else:
        start = getattr(item, "start", None)
        end = getattr(item, "end", None)
        text = getattr(item, "text", "")
    if start is None or end is None:
        return None
    try:
        start_f = float(start)
        end_f = float(end)
    except (TypeError, ValueError):
        return None
    return {
        "start": start_f,
        "end": end_f,
        "text": "" if text is None else str(text).strip(),
        "speaker": "",
    }


def available():
    """faster-whisper 임포트 가능 여부. 예외 없이 True/False 만."""
    try:
        return _import_whisper_model() is not None
    except Exception:
        return False


def transcribe(path, opts=None, on_progress=None, should_cancel=None):
    """오디오 경로를 전사한다. 세그먼트의 speaker 는 항상 빈 문자열.

    opts: {"model", "language", "compute_type", "device"}
    language 가 빈 문자열이면 자동 감지.
    """
    if _cancelled(should_cancel):
        return _fail(ERR_CANCEL)
    try:
        path = "" if path is None else os.fspath(path)
    except (TypeError, ValueError):
        return _fail(ERR_NOT_FOUND)
    if not path or not os.path.isfile(path):
        return _fail(ERR_NOT_FOUND)
    if _cancelled(should_cancel):
        return _fail(ERR_CANCEL)

    WhisperModel = _import_whisper_model()
    if WhisperModel is None:
        return _fail(ERR_NO_LIB)

    cfg = _merged_opts(opts)
    lang = cfg["language"].strip() if isinstance(cfg["language"], str) else cfg["language"]
    language_arg = None if not lang else lang

    if _cancelled(should_cancel):
        return _fail(ERR_CANCEL)
    try:
        model = WhisperModel(
            cfg["model"],
            device=cfg["device"],
            compute_type=cfg["compute_type"],
        )
    except Exception as e:
        _log(f"STT 모델 로드 실패 [{cfg['model']}]: {e}")
        if _is_model_missing(e):
            return _fail(ERR_NO_MODEL)
        return _fail(ERR_TRANSCRIBE)

    if _cancelled(should_cancel):
        return _fail(ERR_CANCEL)
    try:
        segs, info = model.transcribe(
            path, language=language_arg, vad_filter=True,
        )
    except Exception as e:
        _log(f"STT transcribe 실패 [{path}]: {e}")
        if _is_model_missing(e):
            return _fail(ERR_NO_MODEL)
        return _fail(ERR_TRANSCRIBE)

    duration_sec = float(getattr(info, "duration", 0.0) or 0.0)
    language = str(getattr(info, "language", "") or "")
    _report(on_progress, 0.0, duration_sec)

    out = []
    try:
        for raw in segs:
            if _cancelled(should_cancel):
                return _fail(ERR_CANCEL, duration_sec=duration_sec, language=language)
            seg = _as_segment(raw)
            if seg is None:
                continue
            out.append(seg)
            _report(on_progress, seg["end"], duration_sec)
    except Exception as e:
        _log(f"STT 세그먼트 순회 실패 [{path}]: {e}")
        return _fail(ERR_TRANSCRIBE, duration_sec=duration_sec, language=language)

    if duration_sec <= 0 and out:
        duration_sec = float(out[-1]["end"])
    _log(f"STT 전사 완료 [{os.path.basename(path)}] {len(out)}세그먼트 {duration_sec:.1f}초")
    return _ok(out, language, duration_sec)
