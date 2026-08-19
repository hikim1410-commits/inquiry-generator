# -*- coding: utf-8 -*-
"""엔진/모델 설치 상태 — faster-whisper·sherpa-onnx 유무와 화자분리 모델 내려받기.

모델을 exe 에 동봉하지 않는다. 최초 사용 시 GitHub Releases 에서 받아
쓰기 가능한 사용자 데이터 폴더(`src.paths.data_path`)에 둔다.
골격은 `src/convert/kordoc.py`(상태 조회 → 설치 → 확인)를 따른다.

URL 은 `scripts/stt_pilot.py` 의 SEG_URL / EMB_URL 을 그대로 쓴다.
무거운 선택적 의존성은 설치 여부만 프로브하고 모듈 최상단에서 import 하지 않는다.
"""
from __future__ import annotations

import errno
import importlib.util
import os
import shutil
import tarfile
import threading
import urllib.error
import urllib.request

from src.logutil import log as _log
from src.paths import data_path

# k2-fsa/sherpa-onnx GitHub Releases — HF 토큰 불필요. 철자(recongition)는 공식 URL.
SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMB_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-recongition-models/"
           "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx")

SEG_ARCHIVE = "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEG_DIR = "sherpa-onnx-pyannote-segmentation-3-0"
SEG_ONNX = "model.onnx"
EMB_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"

_CHUNK = 64 * 1024
_TIMEOUT = 120
_UA = "NavionQuote-STT"

ERR_INTERRUPTED = "모델 다운로드가 중단되었습니다. 인터넷 연결을 확인한 뒤 다시 시도하세요."
ERR_DISK_FULL = "디스크 공간이 부족합니다. 용량을 확보한 뒤 다시 시도하세요."
ERR_PERMISSION = "모델 폴더에 쓸 권한이 없습니다."
ERR_CORRUPT = "받은 모델 파일이 손상되었습니다. 다시 받아 주세요."
ERR_GENERIC = "모델 설치 중 오류가 발생했습니다."
ERR_STATUS = "엔진 상태를 확인하지 못했습니다."

_install_lock = threading.Lock()


def _engine_installed() -> bool:
    """faster-whisper 설치 여부. 배너 폴링용이라 모듈은 로드하지 않는다."""
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _diarize_installed() -> bool:
    """sherpa-onnx 설치 여부. 모듈은 로드하지 않는다."""
    try:
        return importlib.util.find_spec("sherpa_onnx") is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _model_root() -> str:
    return os.path.abspath(data_path("stt-models"))


def model_dir() -> str:
    """모델을 두는 폴더의 절대경로. 없으면 만들어 반환."""
    d = _model_root()
    os.makedirs(d, exist_ok=True)
    return d


def _nonzero_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _seg_path(root: str) -> str:
    return os.path.join(root, SEG_DIR, SEG_ONNX)


def _emb_path(root: str) -> str:
    return os.path.join(root, EMB_NAME)


def _seg_ready(root: str) -> bool:
    return _nonzero_file(_seg_path(root))


def _emb_ready(root: str) -> bool:
    return _nonzero_file(_emb_path(root))


def _entry(path: str) -> dict:
    present = _nonzero_file(path)
    nbytes = 0
    if present:
        try:
            nbytes = os.path.getsize(path)
        except OSError:
            present = False
            nbytes = 0
    return {"present": present, "path": os.path.abspath(path), "bytes": nbytes}


def _models_dict(root: str) -> dict:
    return {
        "segmentation": _entry(_seg_path(root)),
        "embedding": _entry(_emb_path(root)),
    }


def _empty_models() -> dict:
    return {
        "segmentation": {"present": False, "path": "", "bytes": 0},
        "embedding": {"present": False, "path": "", "bytes": 0},
    }


def status() -> dict:
    """엔진 패키지·화자분리 모델 유무. 미설치여도 예외를 던지지 않는다."""
    try:
        root = _model_root()
        models = _models_dict(root)
        total = int(models["segmentation"]["bytes"]) + int(models["embedding"]["bytes"])
        return {
            "ok": True,
            "engine_installed": bool(_engine_installed()),
            "diarize_installed": bool(_diarize_installed()),
            "models": models,
            "total_bytes": total,
            "error": "",
        }
    except Exception as e:
        _log(f"STT runtime status 실패: {e}")
        return {
            "ok": False,
            "engine_installed": False,
            "diarize_installed": False,
            "models": _empty_models(),
            "total_bytes": 0,
            "error": ERR_STATUS,
        }


def _urlopen(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    return urllib.request.urlopen(req, timeout=_TIMEOUT)


def _remove(path: str) -> None:
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass


def _os_err_msg(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return ERR_PERMISSION
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) == 112:
            return ERR_DISK_FULL
        if exc.errno in (errno.EACCES, errno.EPERM):
            return ERR_PERMISSION
        return ERR_GENERIC
    return ERR_GENERIC


def _open_write(path: str):
    return open(path, "wb")


def _download_file(url: str, dest: str, on_progress=None) -> dict:
    """url → dest. 임시 파일로 받은 뒤 이름을 바꾼다. 실패 시 dest 를 남기지 않는다."""
    parent = os.path.dirname(dest)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except PermissionError:
            return {"ok": False, "error": ERR_PERMISSION}
        except OSError as e:
            return {"ok": False, "error": _os_err_msg(e)}

    tmp = dest + ".part"
    _remove(tmp)
    try:
        with _urlopen(url) as resp:
            total = 0
            headers = getattr(resp, "headers", None)
            cl = headers.get("Content-Length") if headers is not None else None
            if cl:
                try:
                    total = int(cl)
                except (TypeError, ValueError):
                    total = 0
            got = 0
            with _open_write(tmp) as f:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if on_progress:
                        on_progress(got, total)
        if got <= 0:
            _remove(tmp)
            return {"ok": False, "error": ERR_INTERRUPTED}
        if total and got < total:
            _remove(tmp)
            return {"ok": False, "error": ERR_INTERRUPTED}
        os.replace(tmp, dest)
        return {"ok": True, "error": ""}
    except PermissionError:
        _remove(tmp)
        return {"ok": False, "error": ERR_PERMISSION}
    except OSError as e:
        _remove(tmp)
        # URLError·TimeoutError 는 Py3 에서 OSError 하위 클래스다.
        if isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError)):
            return {"ok": False, "error": ERR_INTERRUPTED}
        return {"ok": False, "error": _os_err_msg(e)}
    except Exception as e:
        _remove(tmp)
        _log(f"STT 모델 다운로드 예외: {e}")
        return {"ok": False, "error": ERR_INTERRUPTED}


def _safe_extract(tf, path: str) -> None:
    """경로 탈출을 막고 압축을 푼다. 3.12+ 는 data 필터를 쓴다."""
    try:
        tf.extractall(path, filter="data")
        return
    except TypeError:
        pass
    members = []
    for m in tf.getmembers():
        name = (m.name or "").replace("\\", "/")
        if name.startswith("/") or name.startswith("\\") or ".." in name.split("/"):
            continue
        members.append(m)
    tf.extractall(path, members=members)


def _install_segmentation(root: str, archive_path: str) -> dict:
    """tar.bz2 를 풀어 SEG_DIR/model.onnx 를 만든다. 실패 시 정상 폴더로 오인되지 않게 한다."""
    staging = os.path.join(root, ".seg-extract")
    final = os.path.join(root, SEG_DIR)
    _remove(staging)
    try:
        os.makedirs(staging, exist_ok=True)
        with tarfile.open(archive_path, "r:bz2") as tf:
            _safe_extract(tf, staging)
        extracted = os.path.join(staging, SEG_DIR)
        onnx = os.path.join(extracted, SEG_ONNX)
        if not _nonzero_file(onnx):
            return {"ok": False, "error": ERR_CORRUPT}
        if os.path.isdir(final):
            _remove(final)
        os.replace(extracted, final)
        return {"ok": True, "error": ""}
    except tarfile.TarError:
        return {"ok": False, "error": ERR_CORRUPT}
    except PermissionError:
        return {"ok": False, "error": ERR_PERMISSION}
    except OSError as e:
        return {"ok": False, "error": _os_err_msg(e)}
    finally:
        _remove(staging)
        _remove(archive_path)


def _ensure_models(on_progress=None) -> dict:
    downloaded = []
    try:
        root = model_dir()
    except PermissionError:
        return {"ok": False, "downloaded": [], "error": ERR_PERMISSION}
    except OSError as e:
        return {"ok": False, "downloaded": [], "error": _os_err_msg(e)}

    if not _seg_ready(root):
        archive = os.path.join(root, SEG_ARCHIVE)
        _log(f"STT 세그멘테이션 모델 다운로드: {SEG_ARCHIVE}")
        r = _download_file(SEG_URL, archive, on_progress=on_progress)
        if not r["ok"]:
            _remove(archive)
            return {"ok": False, "downloaded": downloaded, "error": r["error"]}
        r = _install_segmentation(root, archive)
        if not r["ok"] or not _seg_ready(root):
            return {"ok": False, "downloaded": downloaded,
                    "error": r.get("error") or ERR_CORRUPT}
        downloaded.append("segmentation")

    if not _emb_ready(root):
        dest = _emb_path(root)
        _log(f"STT 임베딩 모델 다운로드: {EMB_NAME}")
        r = _download_file(EMB_URL, dest, on_progress=on_progress)
        if not r["ok"] or not _emb_ready(root):
            _remove(dest)
            return {"ok": False, "downloaded": downloaded,
                    "error": r.get("error") or ERR_INTERRUPTED}
        downloaded.append("embedding")

    if downloaded:
        _log("STT 화자분리 모델 준비 완료")
    return {"ok": True, "downloaded": downloaded, "error": ""}


def ensure_models(on_progress=None) -> dict:
    """화자분리 모델을 GitHub Releases 에서 받아 model_dir() 에 둔다. 이미 있으면 건너뛴다.

    on_progress(downloaded_bytes, total_bytes) 는 선택. 파일 단위로 호출된다.
    """
    with _install_lock:
        try:
            return _ensure_models(on_progress=on_progress)
        except PermissionError:
            return {"ok": False, "downloaded": [], "error": ERR_PERMISSION}
        except OSError as e:
            return {"ok": False, "downloaded": [], "error": _os_err_msg(e)}
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            return {"ok": False, "downloaded": [], "error": ERR_INTERRUPTED}
        except Exception as e:
            _log(f"STT ensure_models 예외: {e}")
            return {"ok": False, "downloaded": [], "error": ERR_GENERIC}
