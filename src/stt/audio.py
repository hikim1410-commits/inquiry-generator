# -*- coding: utf-8 -*-
"""오디오 표준화·길이 산출·분할 — PyAV 로 wav 16kHz mono.

ffmpeg.exe 는 쓰지 않는다. 무거운 선택적 의존성(av)은 함수 안에서만 import 한다.
임시 wav 는 호출자가 지운다 — 성공 반환한 경로는 이 모듈이 삭제하지 않는다.
"""
import array
import gc
import math
import os
import tempfile
import wave

from src.logutil import log as _log

TARGET_RATE = 16000
TARGET_CHANNELS = 1
TARGET_SAMPWIDTH = 2  # s16le
DEFAULT_MAX_SEC = 1800

ERR_NO_AV = "음성 변환 라이브러리(PyAV)가 설치되어 있지 않습니다."
ERR_NOT_FOUND = "파일을 찾을 수 없습니다."
ERR_CORRUPT = "파일이 손상되었거나 지원하지 않는 형식입니다."
ERR_NO_AUDIO = "이 파일에는 오디오 트랙이 없습니다. 영상만 있는 파일은 전사할 수 없습니다."
ERR_SILENT = "음성이 감지되지 않았습니다. 무음 파일이거나 녹음이 비어 있습니다."


def _import_av():
    """지연 import. 미설치면 None."""
    try:
        import av
        return av
    except ImportError:
        return None


def _audio_streams(container):
    streams = getattr(container, "streams", None)
    if streams is None:
        return []
    audio = getattr(streams, "audio", None)
    if audio is not None:
        return list(audio)
    return [s for s in streams if getattr(s, "type", None) == "audio"]


def _duration_from_container(container):
    """컨테이너/스트림 메타의 길이(초). 디코딩하지 않는다."""
    dur = getattr(container, "duration", None)
    if dur is not None and dur > 0:
        return float(dur) / 1_000_000.0
    for stream in _audio_streams(container):
        sd = getattr(stream, "duration", None)
        tb = getattr(stream, "time_base", None)
        if sd is not None and sd > 0 and tb is not None:
            return float(sd * tb)
    return 0.0


def _pcm16_has_signal(data):
    """s16 바이트에 0이 아닌 샘플이 있으면 True."""
    if not data:
        return False
    n = len(data) - (len(data) % 2)
    if n <= 0:
        return False
    samples = array.array("h")
    samples.frombytes(data if n == len(data) else data[:n])
    return any(s != 0 for s in samples)


def _frame_pcm(frame):
    planes = getattr(frame, "planes", None)
    if not planes:
        return b""
    return bytes(planes[0])


def _decode_pcm16_mono(av, path):
    """임의 입력을 s16 mono 16kHz PCM 으로 디코딩한다.

    반환: (pcm_bytes, duration_sec, has_audio, silent)
    """
    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=TARGET_RATE,
    )
    chunks = []
    has_signal = False
    used = False
    try:
        with av.open(path, mode="r", metadata_errors="ignore") as container:
            if not _audio_streams(container):
                return b"", 0.0, False, True
            try:
                frame_iter = container.decode(audio=0)
            except Exception:
                return b"", 0.0, True, True
            while True:
                try:
                    frame = next(frame_iter)
                except StopIteration:
                    break
                except Exception:
                    continue
                used = True
                for rf in (resampler.resample(frame) or []):
                    data = _frame_pcm(rf)
                    if not data:
                        continue
                    chunks.append(data)
                    if not has_signal and _pcm16_has_signal(data):
                        has_signal = True
            if used:
                try:
                    for rf in (resampler.resample(None) or []):
                        data = _frame_pcm(rf)
                        if not data:
                            continue
                        chunks.append(data)
                        if not has_signal and _pcm16_has_signal(data):
                            has_signal = True
                except Exception:
                    pass
    finally:
        # PyAV resampler 가 GC 없이 남는 경우가 있다 (faster-whisper#390).
        del resampler
        gc.collect()
    pcm = b"".join(chunks)
    n_samples = len(pcm) // TARGET_SAMPWIDTH
    duration_sec = n_samples / float(TARGET_RATE) if n_samples else 0.0
    silent = (n_samples == 0) or (not has_signal)
    return pcm, duration_sec, True, silent


def _unlink(path):
    """임시 파일을 지운다. 실패해도 삼킨다."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _write_temp_wav(pcm, rate=TARGET_RATE):
    """호출자가 지울 임시 wav 를 만들고 경로를 반환한다."""
    fd, path = tempfile.mkstemp(prefix="stt_norm_", suffix=".wav")
    os.close(fd)
    try:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(TARGET_CHANNELS)
            wf.setsampwidth(TARGET_SAMPWIDTH)
            wf.setframerate(int(rate))
            wf.writeframes(pcm)
        return path
    except Exception:
        _unlink(path)
        raise


def _wav_duration(wav_path):
    with wave.open(wav_path, "rb") as wf:
        rate = wf.getframerate()
        n = wf.getnframes()
        return (n / float(rate)) if rate else 0.0


def _chunk_offsets(duration_sec, max_sec=DEFAULT_MAX_SEC):
    """원본 기준 조각 시작 시각(초) 목록. 짧으면 [0.0] 하나."""
    duration_sec = float(duration_sec or 0.0)
    max_sec = float(max_sec)
    if max_sec <= 0 or duration_sec <= max_sec:
        return [0.0]
    n = int(math.ceil(duration_sec / max_sec))
    return [float(i * max_sec) for i in range(n)]


def probe(path):
    """디코딩 없이 메타만 읽어 길이와 오디오 트랙 유무를 본다."""
    if not path or not os.path.isfile(path):
        return {"ok": False, "duration_sec": 0.0, "has_audio": False,
                "error": ERR_NOT_FOUND}
    av = _import_av()
    if av is None:
        return {"ok": False, "duration_sec": 0.0, "has_audio": False,
                "error": ERR_NO_AV}
    try:
        with av.open(path, mode="r", metadata_errors="ignore") as container:
            has_audio = bool(_audio_streams(container))
            duration_sec = _duration_from_container(container)
            return {"ok": True, "duration_sec": float(duration_sec),
                    "has_audio": has_audio, "error": ""}
    except Exception as e:
        _log(f"STT probe 실패 [{path}]: {e}")
        return {"ok": False, "duration_sec": 0.0, "has_audio": False,
                "error": ERR_CORRUPT}


def normalize(path):
    """임의 오디오/영상 → wav 16kHz mono. 영상은 오디오 트랙만 뽑는다."""
    if not path or not os.path.isfile(path):
        return {"ok": False, "wav_path": "", "duration_sec": 0.0,
                "error": ERR_NOT_FOUND}
    av = _import_av()
    if av is None:
        return {"ok": False, "wav_path": "", "duration_sec": 0.0,
                "error": ERR_NO_AV}
    try:
        pcm, duration_sec, has_audio, silent = _decode_pcm16_mono(av, path)
    except Exception as e:
        _log(f"STT normalize 실패 [{path}]: {e}")
        return {"ok": False, "wav_path": "", "duration_sec": 0.0,
                "error": ERR_CORRUPT}
    if not has_audio:
        return {"ok": False, "wav_path": "", "duration_sec": 0.0,
                "error": ERR_NO_AUDIO}
    if silent:
        return {"ok": False, "wav_path": "", "duration_sec": float(duration_sec),
                "error": ERR_SILENT}
    wav_path = _write_temp_wav(pcm)
    _log(f"STT 오디오 표준화 완료 [{os.path.basename(path)}] {duration_sec:.1f}초")
    return {"ok": True, "wav_path": wav_path, "duration_sec": float(duration_sec),
            "error": ""}


def chunk(wav_path, max_sec=DEFAULT_MAX_SEC):
    """긴 wav 를 max_sec 이하 조각으로 나눈다. 짧으면 원본 1개를 그대로 반환."""
    if not wav_path or not os.path.isfile(wav_path):
        return []
    try:
        duration_sec = _wav_duration(wav_path)
    except Exception as e:
        _log(f"STT chunk 길이 읽기 실패 [{wav_path}]: {e}")
        return [{"path": wav_path, "offset_sec": 0.0}]
    offsets = _chunk_offsets(duration_sec, max_sec)
    if len(offsets) == 1:
        return [{"path": wav_path, "offset_sec": 0.0}]
    try:
        return _split_wav(wav_path, offsets, float(max_sec))
    except Exception as e:
        _log(f"STT chunk 분할 실패 [{wav_path}]: {e}")
        return [{"path": wav_path, "offset_sec": 0.0}]


def _split_wav(wav_path, offsets, max_sec):
    """offsets 의 각 시작 시각부터 max_sec (마지막은 남은 길이) 만큼 잘라 임시 wav 를 만든다.

    중간에 실패하면 이미 만든 조각 파일을 지운다. 지우지 않으면 호출자가
    경로를 받지 못해 60분 회의 조각이 임시 폴더에 남는다.
    """
    out = []
    created = []
    try:
        with wave.open(wav_path, "rb") as src:
            nchannels = src.getnchannels()
            sampwidth = src.getsampwidth()
            framerate = src.getframerate()
            nframes = src.getnframes()
            frames_per_chunk = int(round(max_sec * framerate)) if framerate else 0
            for i, offset in enumerate(offsets):
                start = int(round(float(offset) * framerate)) if framerate else 0
                if start >= nframes:
                    break
                if i + 1 < len(offsets) and frames_per_chunk > 0:
                    count = min(frames_per_chunk, nframes - start)
                else:
                    count = nframes - start
                src.setpos(start)
                data = src.readframes(count)
                fd, dest = tempfile.mkstemp(prefix="stt_chunk_", suffix=".wav")
                os.close(fd)
                created.append(dest)
                with wave.open(dest, "wb") as dst:
                    dst.setnchannels(nchannels)
                    dst.setsampwidth(sampwidth)
                    dst.setframerate(framerate)
                    dst.writeframes(data)
                out.append({"path": dest, "offset_sec": float(offset)})
        return out if out else [{"path": wav_path, "offset_sec": 0.0}]
    except Exception:
        for path in created:
            _unlink(path)
        raise
