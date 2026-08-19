# -*- coding: utf-8 -*-
"""화자 분리 + 세그먼트 결합 — sherpa-onnx 오프라인 화자분리.

pyannote.audio 는 쓰지 않는다(HF 게이트). 같은 세그멘테이션 모델의 ONNX 변환본을
sherpa-onnx 가 GitHub Releases 로 배포한다.

assign/merge 는 sherpa-onnx 가 없어도 동작하는 순수 함수다.
무거운 의존성(sherpa_onnx, numpy)은 함수 안에서만 import 한다.
"""
import os
import wave

from src.logutil import log as _log

# scripts/stt_pilot.py 와 동일한 파일명 — T5 runtime.ensure_models() 가 이 위치에 둔다.
SEG_REL = os.path.join("sherpa-onnx-pyannote-segmentation-3-0", "model.onnx")
EMB_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"

# 파일럿 실측: 공식 예제 기본값 0.5는 화자를 과분할했다. 0.9에서 정답과 일치.
CLUSTER_THRESHOLD = 0.9
MIN_DURATION_ON = 0.3
MIN_DURATION_OFF = 0.5

ERR_NO_SHERPA = "화자 분리 엔진(sherpa-onnx)이 설치되어 있지 않습니다."
ERR_NO_RUNTIME = "화자 분리 모델 관리 모듈이 없습니다. 엔진 설치를 먼저 진행하세요."
ERR_NO_MODELS = "화자 분리 모델이 설치되어 있지 않습니다. 설정에서 모델을 내려받으세요."
ERR_NOT_FOUND = "음성 파일을 찾을 수 없습니다."
ERR_BAD_WAV = "wav 파일을 읽을 수 없습니다. 오디오를 먼저 변환하세요."
ERR_SILENT = "음성이 감지되지 않았습니다. 무음 파일이거나 녹음이 비어 있습니다."
ERR_FAIL = "화자 분리에 실패했습니다. 파일을 확인하거나 엔진을 다시 설치하세요."
ERR_NO_NUMPY = "음성 처리 라이브러리(numpy)가 설치되어 있지 않습니다."


def _import_sherpa():
    """지연 import. 미설치면 None."""
    try:
        import sherpa_onnx
        return sherpa_onnx
    except ImportError:
        return None


def _get_model_dir():
    """T5 runtime.model_dir(). 없으면 ImportError."""
    from src.stt.runtime import model_dir
    return model_dir()


def _fail(error):
    return {"ok": False, "turns": [], "num_speakers": 0, "error": error}


def _label_speaker(raw):
    """화자 식별자를 '화자N' 한국어 라벨로 만든다. 비면 빈 문자열.

    sherpa-onnx 는 0부터 세는 정수 ID 를 낸다. 이미 '화자1' 형태면 그대로 둔다.
    """
    if raw is None or isinstance(raw, bool):
        return ""
    if isinstance(raw, (int, float)):
        n = int(raw)
        return f"화자{n + 1}" if n >= 0 else ""
    s = str(raw).strip()
    if not s:
        return ""
    if s.startswith("화자"):
        return s
    if s.isdigit() or (s[0] == "-" and s[1:].isdigit()):
        n = int(s)
        return f"화자{n + 1}" if n >= 0 else ""
    return s


def _as_sec(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _overlap(a0, a1, b0, b1):
    """두 구간이 겹치는 시간(초). 맞닿기만 하면 0."""
    return max(0.0, min(a1, b1) - max(a0, b0))


def _nonzero_file(path):
    """0바이트 파일을 설치된 모델로 오인하지 않는다 (runtime._nonzero_file 과 동일)."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _model_paths(model_dir):
    """세그멘테이션·임베딩 onnx 경로. 없거나 비어 있으면 빈 문자열."""
    if not model_dir:
        return "", ""
    seg = os.path.join(model_dir, SEG_REL)
    emb = os.path.join(model_dir, EMB_NAME)
    if _nonzero_file(seg) and _nonzero_file(emb):
        return seg, emb
    return "", ""


def _load_wav_f32(path):
    """16bit wav → float32 모노 배열, 샘플레이트.

    반환: (samples, rate, error). 실패 시 samples 는 None.
    """
    try:
        with wave.open(path, "rb") as wf:
            nch = wf.getnchannels()
            sw = wf.getsampwidth()
            rate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
    except Exception:
        return None, 0, ERR_BAD_WAV
    if sw != 2 or nch < 1 or rate <= 0:
        return None, 0, ERR_BAD_WAV
    n = len(raw) - (len(raw) % 2)
    if n <= 0:
        return None, 0, ERR_SILENT
    try:
        import numpy as np
    except ImportError:
        return None, 0, ERR_NO_NUMPY
    samples = np.frombuffer(raw[:n], dtype=np.int16).astype(np.float32) / 32768.0
    if nch > 1:
        usable = (samples.size // nch) * nch
        if usable <= 0:
            return None, 0, ERR_SILENT
        samples = samples[:usable].reshape(-1, nch)[:, 0].copy()
    if samples.size == 0:
        return None, 0, ERR_SILENT
    return samples, int(rate), ""


def _pcm16_has_signal(samples):
    """float32 배열에 0이 아닌 샘플이 있으면 True."""
    try:
        return bool((samples != 0).any())
    except Exception:
        return False


def available():
    """sherpa-onnx 를 import 할 수 있으면 True."""
    return _import_sherpa() is not None


def assign(segments, turns):
    """전사 세그먼트에, 겹치는 시간이 가장 긴 화자 라벨을 붙인다.

    겹치는 화자가 없으면 speaker 는 빈 문자열. 입력을 변경하지 않는다.
    """
    out = []
    turn_list = list(turns or [])
    for seg in list(segments or []):
        if not isinstance(seg, dict):
            continue
        start = _as_sec(seg.get("start"))
        end = _as_sec(seg.get("end"))
        text = "" if seg.get("text") is None else str(seg.get("text"))
        best_label = ""
        best_ov = 0.0
        for turn in turn_list:
            if not isinstance(turn, dict):
                continue
            ov = _overlap(start, end,
                          _as_sec(turn.get("start")), _as_sec(turn.get("end")))
            if ov > best_ov:
                best_ov = ov
                best_label = _label_speaker(turn.get("speaker"))
        out.append({
            "start": start,
            "end": end,
            "text": text,
            "speaker": best_label if best_ov > 0.0 else "",
        })
    return out


def merge(segments):
    """같은 화자의 연속 세그먼트를 하나로 합친다.

    text 는 공백으로 이어 붙이고, start 는 첫 세그먼트, end 는 마지막 세그먼트.
    화자가 바뀌면 합치지 않는다. 빈 화자끼리는 합치지 않는다.
    """
    out = []
    for seg in list(segments or []):
        if not isinstance(seg, dict):
            continue
        start = _as_sec(seg.get("start"))
        end = _as_sec(seg.get("end"))
        text = "" if seg.get("text") is None else str(seg.get("text"))
        speaker = _label_speaker(seg.get("speaker"))
        if out and speaker and speaker == out[-1]["speaker"]:
            prev = out[-1]
            prev["end"] = end
            parts = [p.strip() for p in (prev["text"], text) if p and str(p).strip()]
            prev["text"] = " ".join(parts)
        else:
            out.append({
                "start": start,
                "end": end,
                "text": text,
                "speaker": speaker,
            })
    return out


def diarize(wav_path, num_speakers=-1):
    """wav(16kHz mono 권장)에서 화자 구간(Turn)을 뽑는다.

    num_speakers=-1 이면 화자 수를 자동 추정한다.
    """
    if not wav_path or not os.path.isfile(wav_path):
        return _fail(ERR_NOT_FOUND)
    sherpa = _import_sherpa()
    if sherpa is None:
        return _fail(ERR_NO_SHERPA)
    try:
        model_dir = _get_model_dir()
    except ImportError:
        return _fail(ERR_NO_RUNTIME)
    except Exception as e:
        _log(f"STT model_dir 실패: {e}")
        return _fail(ERR_NO_RUNTIME)
    seg_path, emb_path = _model_paths(model_dir)
    if not seg_path or not emb_path:
        return _fail(ERR_NO_MODELS)
    samples, rate, err = _load_wav_f32(wav_path)
    if err:
        return _fail(err)
    if not _pcm16_has_signal(samples):
        return _fail(ERR_SILENT)
    try:
        n_spk = int(num_speakers)
    except (TypeError, ValueError):
        n_spk = -1
    try:
        turns = _run_diarization(
            sherpa, samples, rate, n_spk, seg_path, emb_path)
    except Exception as e:
        _log(f"STT 화자 분리 실패 [{wav_path}]: {e}")
        return _fail(ERR_FAIL)
    speakers = {t["speaker"] for t in turns if t.get("speaker")}
    return {
        "ok": True,
        "turns": turns,
        "num_speakers": len(speakers),
        "error": "",
    }


def _run_diarization(sherpa, samples, rate, num_speakers, seg_path, emb_path):
    """sherpa-onnx OfflineSpeakerDiarization 을 돌리고 Turn 목록을 반환한다."""
    config = sherpa.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(seg_path)),
        ),
        embedding=sherpa.SpeakerEmbeddingExtractorConfig(model=str(emb_path)),
        clustering=sherpa.FastClusteringConfig(
            num_clusters=int(num_speakers), threshold=CLUSTER_THRESHOLD),
        min_duration_on=MIN_DURATION_ON,
        min_duration_off=MIN_DURATION_OFF,
    )
    validate = getattr(config, "validate", None)
    if callable(validate) and not validate():
        raise RuntimeError("화자 분리 설정 검증 실패")
    sd = sherpa.OfflineSpeakerDiarization(config)
    expected = int(getattr(sd, "sample_rate", 16000) or 16000)
    if int(rate) != expected:
        raise RuntimeError(
            f"샘플레이트 불일치: 기대 {expected}Hz, 입력 {rate}Hz")
    result = sd.process(samples)
    sort_fn = getattr(result, "sort_by_start_time", None)
    if callable(sort_fn):
        result = sort_fn()
    turns = []
    for r in result:
        start = _as_sec(getattr(r, "start", 0.0))
        end = _as_sec(getattr(r, "end", 0.0))
        label = _label_speaker(getattr(r, "speaker", ""))
        turns.append({"start": start, "end": end, "speaker": label})
    return turns
