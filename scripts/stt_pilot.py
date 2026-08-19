# -*- coding: utf-8 -*-
"""회의록 STT 파일럿 — 앱 코드 아님. PRD §12.1 게이트 판정용 임시 스크립트.

faster-whisper(전사) + sherpa-onnx(화자분리)를 실제 회의 녹음에 돌려
PRD §10 Q5~Q9(한영 혼용 / 분당 글자수 / 처리시간 / 설치용량)를 실측한다.

준비(앱 환경을 더럽히지 않게 별도 venv):
    python -m venv .venv-stt
    .venv-stt/Scripts/pip install faster-whisper sherpa-onnx
    .venv-stt/Scripts/python scripts/stt_pilot.py 녹음1.m4a 녹음2.m4a

옵션:
    --lang ko          언어 강제(기본: 자동 감지 — Q5 A/B용)
    --model small      whisper 모델 크기(기본 medium)
    --speakers N       화자 수 고정(기본 -1 = 자동 추정)
    --self-test        화자 매칭 로직만 검증하고 종료
    --json-out PATH    통계 JSON 저장 위치(기본: 각 wav 옆 .{model}.pilot.json)
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")   # Windows 콘솔 기본 cp949에서 한글·통계 깨짐 방지

CACHE = Path(__file__).resolve().parent.parent / ".stt-pilot-cache"

# sherpa-onnx 공식 예제(python-api-examples/offline-speaker-diarization.py) 명시 URL.
# HF 게이트 없는 GitHub Releases — 이게 pyannote 직접 사용을 배제한 이유다.
SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMB_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-recongition-models/"
           "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx")


def _download(url: str) -> Path:
    dest = CACHE / url.rsplit("/", 1)[-1]
    if not dest.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        print(f"  받는 중: {dest.name}", flush=True)
        urllib.request.urlretrieve(url, dest)
    return dest


def ensure_models() -> tuple[Path, Path]:
    """세그멘테이션·임베딩 모델 경로를 반환(없으면 다운로드·압축해제)."""
    seg_dir = CACHE / "sherpa-onnx-pyannote-segmentation-3-0"
    if not seg_dir.exists():
        with tarfile.open(_download(SEG_URL)) as tf:
            try:
                tf.extractall(CACHE, filter="data")
            except TypeError:
                tf.extractall(CACHE)
    return seg_dir / "model.onnx", _download(EMB_URL)


def assign_speakers(segments, turns):
    """전사 세그먼트마다 시간 겹침이 가장 큰 화자를 붙인다.

    segments: [(start, end, text)], turns: [(start, end, speaker)]
    겹치는 화자가 없으면 speaker=None.
    """
    out = []
    for s, e, text in segments:
        best, best_ov = None, 0.0
        for ts, te, spk in turns:
            ov = min(e, te) - max(s, ts)
            if ov > best_ov:
                best, best_ov = spk, ov
        out.append((s, e, best, text))
    return out


def transcribe(path: Path, args):
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio  # PyAV 내장 — ffmpeg.exe 불필요

    t0 = time.perf_counter()
    audio = decode_audio(str(path), sampling_rate=16000)
    t_decode = time.perf_counter() - t0

    t0 = time.perf_counter()
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    segs, info = model.transcribe(audio, language=args.lang, vad_filter=True)
    segments = [(s.start, s.end, s.text.strip()) for s in segs]
    t_asr = time.perf_counter() - t0

    t0 = time.perf_counter()
    turns = diarize(audio, args)
    t_diar = time.perf_counter() - t0

    return {
        "audio": audio, "info": info, "segments": segments, "turns": turns,
        "t_decode": t_decode, "t_asr": t_asr, "t_diar": t_diar,
    }


def diarize(audio, args):
    import sherpa_onnx

    seg_model, emb_model = ensure_models()
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(seg_model)),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model)),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=args.speakers, threshold=args.cluster_threshold),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    assert sd.sample_rate == 16000, f"디코딩 샘플레이트 불일치: {sd.sample_rate}"
    return [(s.start, s.end, s.speaker) for s in sd.process(audio).sort_by_start_time()]


def mmss(sec: float) -> str:
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


def _dir_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


def _hf_cache_root() -> Path:
    return Path.home() / ".cache" / "huggingface"


def run_one(path: Path, args) -> dict:
    print(f"\n=== {path.name}  model={args.model}  lang={args.lang!r} ===",
          flush=True)
    r = transcribe(path, args)
    lines = assign_speakers(r["segments"], r["turns"])

    body = "\n".join(
        f"[{mmss(s)}] 화자{spk if spk is not None else '?'}: {text}"
        for s, _e, spk, text in lines)
    tag = args.model
    out = path.with_name(f"{path.stem}.{tag}.pilot.txt")
    out.write_text(body, encoding="utf-8")

    dur = len(r["audio"]) / 16000
    chars = sum(len(t) for *_x, t in lines)
    n_spk = len({spk for _s, _e, spk in r["turns"]})
    t_asr = r["t_asr"]
    t_diar = r["t_diar"]
    stat = {
        "file": path.name,
        "model": args.model,
        "lang": args.lang,
        "녹음길이_초": round(dur, 2),
        "녹음길이_분": round(dur / 60, 3),
        "감지언어": r["info"].language,
        "언어확률": round(r["info"].language_probability, 3),
        "전사글자수": chars,
        "분당글자수": round(chars / (dur / 60), 1) if dur else 0,   # Q6
        "추정화자수": n_spk,
        "화자id목록": sorted({int(spk) for _s, _e, spk in r["turns"]}),
        "디코딩초": round(r["t_decode"], 1),
        "전사초": round(t_asr, 1),                                  # Q7
        "화자분리초": round(t_diar, 1),
        "처리초합": round(t_asr + t_diar, 1),
        "오디오1분당처리초": round((t_asr + t_diar) / (dur / 60), 1) if dur else 0,
        "실시간배속": round(dur / (t_asr + t_diar), 2) if (t_asr + t_diar) else 0,
        "출력": str(out),
        "세그먼트수": len(r["segments"]),
        "턴수": len(r["turns"]),
    }
    detail = {
        "stat": stat,
        "segments": [
            {"start": s, "end": e, "text": t} for s, e, t in r["segments"]
        ],
        "turns": [
            {"start": s, "end": e, "speaker": spk} for s, e, spk in r["turns"]
        ],
        "lines": [
            {"start": s, "end": e, "speaker": spk, "text": t}
            for s, e, spk, t in lines
        ],
    }
    js = path.with_name(f"{path.stem}.{tag}.pilot.json")
    js.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    stat["json"] = str(js)
    print(json.dumps(stat, ensure_ascii=False, indent=2), flush=True)
    return stat


def _self_test():
    turns = [(0.0, 5.0, 0), (5.0, 10.0, 1)]
    got = assign_speakers([(0.5, 2.0, "가"), (6.0, 9.0, "나"), (20.0, 21.0, "다")], turns)
    assert [g[2] for g in got] == [0, 1, None], got
    # 경계를 걸친 세그먼트는 겹침이 더 큰 쪽으로
    assert assign_speakers([(4.0, 9.0, "x")], turns)[0][2] == 1
    print("self-test OK")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="*", type=Path)
    p.add_argument("--lang", default=None, help="ko/en 강제(기본 자동)")
    p.add_argument("--model", default="medium")
    p.add_argument("--speakers", type=int, default=-1, help="-1이면 자동 추정")
    # sherpa 예제 기본값 0.5는 4인 테스트 음원을 7명으로 과분할했다(실측 2026-08-19).
    # 0.9에서 정답과 일치. 단 중국어 음원 + zh 임베딩 모델 기준이므로 한국어 회의로 재보정 필요.
    p.add_argument("--cluster-threshold", type=float, default=0.9)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--json-out", type=Path, default=None,
                   help="전체 통계 JSON 경로 (기본: .stt-pilot-cache/stt_pilot_stats.json)")
    args = p.parse_args()

    if args.self_test:
        _self_test()
        return
    if not args.files:
        p.error("녹음 파일 경로를 하나 이상 주세요 (또는 --self-test)")

    stats = [run_one(f, args) for f in args.files]

    sizes = {}
    venv = Path(sys.prefix)
    if venv != Path(sys.base_prefix):   # Q9 — 설치 용량
        sizes["venv_bytes"] = _dir_bytes(venv)
        sizes["venv"] = str(venv)
        print(f"\n의존성 설치 용량: {sizes['venv_bytes'] / 1e6:.0f} MB ({venv})")
    if CACHE.exists():
        sizes["sherpa_cache_bytes"] = _dir_bytes(CACHE)
        sizes["sherpa_cache"] = str(CACHE)
        print(f"sherpa 모델 캐시: {sizes['sherpa_cache_bytes'] / 1e6:.0f} MB ({CACHE})")
    hf = _hf_cache_root()
    if hf.exists():
        sizes["hf_cache_bytes"] = _dir_bytes(hf)
        sizes["hf_cache"] = str(hf)
        print(f"HuggingFace 캐시: {sizes['hf_cache_bytes'] / 1e6:.0f} MB ({hf})")
        hub = hf / "hub"
        if hub.exists():
            for child in sorted(hub.iterdir()):
                if child.is_dir() and "faster-whisper" in child.name:
                    b = _dir_bytes(child)
                    print(f"  {child.name}: {b / 1e6:.0f} MB")

    payload = {"runs": stats, "sizes": sizes}
    summary = args.json_out or (CACHE / "stt_pilot_stats.json")
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(f"통계 요약: {summary}")


if __name__ == "__main__":
    main()
