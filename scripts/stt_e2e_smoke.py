# -*- coding: utf-8 -*-
"""앱 공개 API 경로로 STT 전 구간을 실제로 돌리는 스모크.

파일럿(scripts/stt_pilot.py)과 달리 monkeypatch 없이 src.api.Api 만 쓴다.
입력은 scripts/stt_selftest.py 가 만든 합성 2화자 wav.

    python scripts/stt_e2e_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import wave
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WAV = ROOT / ".stt-pilot-cache" / "selftest" / "meeting_2spk.wav"
SCRIPT = ROOT / ".stt-pilot-cache" / "selftest" / "meeting_2spk.script.txt"
OUT_DIR = ROOT / ".stt-pilot-cache" / "e2e-smoke"
POLL_SEC = 2.0
TIMEOUT_SEC = 2400.0  # 40분 — medium CPU 전사 + 화자분리


def _log(step, payload):
    print(f"\n=== {step} ===", flush=True)
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
              flush=True)
    else:
        print(payload, flush=True)


def _ensure_wav():
    if WAV.is_file() and WAV.stat().st_size > 1000:
        return str(WAV)
    print("합성 wav 가 없어 scripts/stt_selftest.py 를 실행합니다.", flush=True)
    import runpy
    runpy.run_path(str(ROOT / "scripts" / "stt_selftest.py"), run_name="__main__")
    if not WAV.is_file():
        raise SystemExit("합성 wav 를 만들지 못했습니다.")
    return str(WAV)


def _wav_duration(path):
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        n = wf.getnframes()
        return (n / float(rate)) if rate else 0.0


def _fail(reason, extra=None):
    _log("실패", reason)
    if extra is not None:
        _log("부가", extra)
    print("\nSMOKE FAIL", flush=True)
    raise SystemExit(1)


def main():
    os.chdir(ROOT)
    wav = _ensure_wav()
    dur = _wav_duration(wav)
    _log("입력", {"wav": wav, "bytes": os.path.getsize(wav),
                 "duration_sec": round(dur, 1)})

    from src.api import Api
    api = Api()

    st = api.stt_status()
    _log("stt_status", st)
    if not st.get("ok"):
        _fail("stt_status 실패", st)

    inst = api.install_stt_models()
    _log("install_stt_models", inst)
    if not inst.get("ok"):
        _fail("모델 설치 실패", inst)

    started = api.start_transcribe({"paths": [wav]})
    _log("start_transcribe", started)
    if not started.get("ok"):
        _fail("전사 시작 실패", started)

    t0 = time.time()
    seen_pct = []
    last_key = None
    status = {}
    while True:
        status = api.transcribe_status()
        key = (status.get("phase"), status.get("pct"), status.get("running"))
        if key != last_key:
            last_key = key
            seen_pct.append(float(status.get("pct") or 0.0))
            print(
                f"  [{time.time()-t0:6.1f}s] phase={status.get('phase')} "
                f"pct={status.get('pct')} running={status.get('running')} "
                f"err={status.get('error')!r}",
                flush=True)
        if not status.get("running"):
            break
        if time.time() - t0 > TIMEOUT_SEC:
            _fail("전사 제한시간 초과", status)
        time.sleep(POLL_SEC)

    _log("transcribe_status 최종", status)
    if status.get("error") and status.get("phase") != "done":
        _fail("전사가 오류로 끝남", status)

    tr = api.get_transcript()
    segs = tr.get("segments") or []
    speakers = tr.get("speakers") or []
    files = tr.get("files") or []
    texts = [str(s.get("text") or "") for s in segs]
    joined = " ".join(texts)
    _log("get_transcript 요약", {
        "ok": tr.get("ok"),
        "n_segments": len(segs),
        "speakers": speakers,
        "file_ok": [f.get("ok") for f in files],
        "file_error": [f.get("error") for f in files],
        "file_warning": [f.get("warning") for f in files],
        "first_lines": [
            f"[{s.get('ts')}] {s.get('speaker')}: {s.get('text')}"
            for s in segs[:8]
        ],
    })
    if not tr.get("ok"):
        _fail("get_transcript 실패", tr)
    if not segs:
        _fail("세그먼트가 비어 있다", tr)

    hangul = sum(1 for ch in joined if "가" <= ch <= "힣")
    bad_ts = []
    for s in segs:
        a, b = float(s.get("start") or 0.0), float(s.get("end") or 0.0)
        if a < -0.05 or b > dur + 1.0 or b < a:
            bad_ts.append((a, b))

    mapping = {"화자1": "내비온 김형일", "화자2": "KIST 김종민"}
    mapped = api.set_speaker_names({"mapping": mapping})
    mapped_text = " ".join(
        str(s.get("speaker") or "") + ":" + str(s.get("text") or "")
        for s in (mapped.get("segments") or []))
    _log("set_speaker_names 샘플", [
        f"[{s.get('ts')}] {s.get('speaker')}: {s.get('text')}"
        for s in (mapped.get("segments") or [])[:6]
    ])
    if not mapped.get("ok"):
        _fail("화자 이름 매핑 실패", mapped)

    minutes = api.transcript_to_minutes()
    _log("transcript_to_minutes", {
        "ok": minutes.get("ok"),
        "chars": minutes.get("chars"),
        "head": (minutes.get("text") or "")[:400],
    })
    if not minutes.get("ok"):
        _fail("회의록 입력 변환 실패", minutes)
    body = minutes.get("text") or minutes.get("description") or ""
    if "[회의 전사본]" not in body:
        _fail("회의록 입력에 [회의 전사본] 표제가 없다", minutes)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = api.save_transcript({"folder": str(OUT_DIR), "stem": "e2e"})
    _log("save_transcript", saved)
    if not saved.get("ok"):
        _fail("사이드카 저장 실패", saved)
    json_path = saved.get("json_path") or ""
    txt_path = saved.get("txt_path") or ""
    if not (json_path and os.path.isfile(json_path) and
            txt_path and os.path.isfile(txt_path)):
        _fail("사이드카 파일이 디스크에 없다", saved)

    pct_moved = (min(seen_pct) < 50.0 and max(seen_pct) >= 99.0
                 if seen_pct else False)
    names_applied = (
        ("내비온 김형일" in mapped_text) or ("KIST 김종민" in mapped_text)
    )

    report = {
        "hangul_chars": hangul,
        "n_speakers": len(speakers),
        "speakers": speakers,
        "n_segments": len(segs),
        "bad_timestamps": bad_ts[:8],
        "progress_samples": seen_pct[:20],
        "progress_moved": pct_moved,
        "names_applied": names_applied,
        "minutes_has_header": "[회의 전사본]" in body,
        "sidecar_json": json_path,
        "sidecar_txt": txt_path,
        "quote": joined[:180],
    }
    _log("판정 재료", report)

    problems = []
    if hangul < 20:
        problems.append("한국어가 거의 없다")
    if len(speakers) < 1:
        problems.append("화자가 없다")
    if bad_ts:
        problems.append(f"시각이 범위를 벗어남 {bad_ts[:3]}")
    if not names_applied:
        problems.append("화자 실명이 반영되지 않았다")
    if not pct_moved:
        problems.append("진행률이 0→100으로 움직이지 않았다")

    if problems:
        _fail("; ".join(problems), report)

    print("\nSMOKE PASS", flush=True)
    print(f"인용: {joined[:180]}", flush=True)
    print(f"화자 {len(speakers)}명 {speakers}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail(f"예외: {type(e).__name__}: {e}")
