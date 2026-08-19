# -*- coding: utf-8 -*-
"""합성 2화자 한국어 회의 음성 — STT 파일럿(Q5~Q9) 입력용.

Windows SAPI TTS(Heami)로 3~5분 wav 를 만든다. 앱 코드가 아니다.
출력은 `.stt-pilot-cache/selftest/` (gitignore 대상 — N2 담당).

    python scripts/stt_selftest.py
    python scripts/stt_selftest.py --out-dir D:\\tmp\\stt-selftest
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / ".stt-pilot-cache" / "selftest"

# SAPI 상수 (SpeechLib)
SSFMCreateForWrite = 3
SAFT16kHz16BitMono = 18
SVSFIsXML = 8

# 화자 A: 내비온 김형일 / 화자 B: KIST 김종민
# 한국어 SAPI 는 Heami 한 명뿐(실측)이라 B 는 Rate·Pitch 로 구분한다.
TURNS = [
    ("A", "안녕하십니까. 주식회사 내비온의 김형일입니다. 오늘은 이천이십육년 팔월 십구일, "
          "저가의 고효율 라이다 센서 사업 타당성 분석 용역 착수 회의를 시작하겠습니다."),
    ("B", "KIST 광기술연구소 김종민입니다. 오늘 안건은 LiDAR 센서의 TRL 수준과 "
          "R&D 일정을 확정하는 것입니다. KPI 초안도 같이 보겠습니다."),
    ("A", "네. 본 용역 계약 금액은 일억 이천오백만 원이고, 수행 기간은 "
          "이천이십육년 구월 일일부터 이천이십칠년 이월 이십팔일까지입니다. "
          "Kickoff 는 구월 십오일 오후에 잡았습니다."),
    ("B", "TRL 기준으로 보면 현재 시제품은 level 5 정도입니다. "
          "연말 PoC 를 통과하면 level 6 으로 올릴 수 있습니다. "
          "다만 outdoor 환경의 SNR 이 아직 부족합니다."),
    ("A", "정부 R&D 과제 협약서 상의 마일스톤은 십이월 중간점검, "
          "이천이십칠년 일월 최종 보고입니다. 중간점검 때 MOU 초안도 제출해야 합니다."),
    ("B", "MOU 상대는 현대차 남양연구소입니다. 영문 명칭은 Hyundai Motor NAMYANG R&D Center 입니다. "
          "NDA 는 이미 체결했고, SLA 초안은 아직입니다."),
    ("A", "알겠습니다. 인건비 구성을 말씀드리면 책임연구원 일 명, 연구원 이 명, "
          "연구보조원 일 명입니다. 책임연구원 단가는 월 칠백오십육만 칠천사백오십육 원입니다."),
    ("B", "그 단가는 학술연구용역 인건비 기준에 맞습니다. "
          "다만 KPI 의 정량 지표를 더 분명하게 써야 합니다. "
          "예를 들어 detection range 백오십 미터, frame rate 십 헤르츠, "
          "단가 목표 대당 팔십만 원입니다."),
    ("A", "네, KPI 표에 반영하겠습니다. 영어 약어는 회의록에 그대로 남기겠습니다. "
          "R&D, MOU, KPI, PoC, LiDAR, TRL, SLA, NDA, SNR 입니다."),
    ("B", "좋습니다. 기술 리스크는 두 가지입니다. 첫째, 눈과 비에서 point cloud 가 깨집니다. "
          "둘째, GPU 없이 임베디드에서 real-time inference 가 빠듯합니다. "
          "Edge TPU 대안을 검토해야 합니다."),
    ("A", "예산 여유는 약 팔백만 원입니다. 시제품 추가 제작에 쓸 수 있습니다. "
          "구매는 십일월 십일까지 발주해야 연내 입고가 됩니다."),
    ("B", "일정에 동의합니다. 다음 주 화요일 오전 열 시에 technical review 를 합시다. "
          "자료는 월요일까지 공유 드라이브에 올려 주세요. 파일명은 "
          "Navion_LiDAR_R&D_v0.3.pptx 로 통일합시다."),
    ("A", "회의록 참석자는 내비온 김형일, 내비온 조명한, KIST 김종민, "
          "KIST 이민아 네 명입니다. 오늘은 두 분만 참석하셨습니다."),
    ("B", "액션 아이템을 정리합니다. 하나, 내비온은 KPI 표를 수정한다. "
          "둘, KIST 는 TRL 증빙 자료를 보낸다. 셋, 양측은 MOU 영문 드래프트를 "
          "구월 오일까지 교환한다."),
    ("A", "추가합니다. 네, 중간점검 발표 자료는 십일월 이십일까지 초안을 돌린다. "
          "다섯, 보안 때문에 원본 녹음은 외부 STT 클라우드로 보내지 않는다. "
          "로컬 전사만 사용한다."),
    ("B", "동의합니다. 대외비 단가와 인건비가 오가는 회의입니다. "
          "off the record 로 남기지 말고, 회의록 확정 후에만 배포합시다."),
    ("A", "마지막으로 다음 회의는 이천이십육년 구월 오일 오후 두 시, "
          "내비온 삼호빌딩 사층 회의실입니다. 온라인 병행은 Zoom 이 아니라 "
          "사내 Teams 를 씁니다."),
    ("B", "확인했습니다. 오늘 논의한 핵심은 LiDAR R&D 의 TRL 오에서 육, "
          "PoC 연말, MOU 는 현대차 남양연구소, KPI 는 range 와 단가입니다. "
          "이상입니다."),
    ("A", "네, 이상으로 착수 회의를 마치겠습니다. 수고하셨습니다."),
    ("B", "수고하셨습니다. 자료는 오늘 저녁까지 올리겠습니다."),
]


def _xml_wrap(text: str, pitch: int) -> str:
    if pitch == 0:
        return text
    return f'<pitch absmiddle="{int(pitch)}">{xml_escape(text)}</pitch>'


def list_voices():
    import win32com.client
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    col = voice.GetVoices()
    rows = []
    for i in range(col.Count):
        item = col.Item(i)
        lang = ""
        try:
            lang = str(item.GetAttribute("Language") or "")
        except Exception:
            lang = ""
        rows.append({
            "index": i,
            "desc": item.GetDescription(),
            "lang": lang,          # 412 = ko-KR, 409 = en-US
        })
    return rows


def _pick_korean_index(rows) -> int | None:
    for r in rows:
        if r["lang"] == "412" or "Korean" in (r["desc"] or ""):
            return r["index"]
    return None


def speak_to_wav(text: str, dest: Path, voice_index: int, rate: int = 0,
                 pitch: int = 0) -> None:
    import win32com.client
    dest.parent.mkdir(parents=True, exist_ok=True)
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = SAFT16kHz16BitMono
    stream.Open(str(dest), SSFMCreateForWrite)
    try:
        voice.AudioOutputStream = stream
        voice.Voice = voice.GetVoices().Item(int(voice_index))
        voice.Rate = int(rate)
        flags = SVSFIsXML if pitch else 0
        voice.Speak(_xml_wrap(text, pitch), flags)
    finally:
        stream.Close()


def concat_wavs(paths, dest: Path, silence_ms: int = 650) -> dict:
    """16kHz 16bit mono wav 들을 무음 간격으로 이어 붙인다."""
    frames = []
    params = None
    for p in paths:
        with wave.open(str(p), "rb") as wf:
            cur = wf.getparams()
            if params is None:
                params = cur
            elif (cur.nchannels, cur.sampwidth, cur.framerate) != (
                    params.nchannels, params.sampwidth, params.framerate):
                raise RuntimeError(f"wav 포맷 불일치: {p}")
            frames.append(wf.readframes(wf.getnframes()))
            n = int(params.framerate * (silence_ms / 1000.0))
            frames.append(b"\x00" * (n * params.sampwidth * params.nchannels))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "wb") as out:
        out.setparams(params)
        out.writeframes(b"".join(frames))
    nframes = 0
    with wave.open(str(dest), "rb") as wf:
        nframes = wf.getnframes()
        rate = wf.getframerate()
    dur = nframes / float(rate) if rate else 0.0
    return {"path": str(dest), "duration_sec": dur, "rate": rate}


def write_script(dest: Path) -> dict:
    lines = []
    names = {"A": "내비온 김형일", "B": "KIST 김종민"}
    chars = 0
    for spk, text in TURNS:
        line = f"[{names[spk]}] {text}"
        lines.append(line)
        chars += len(text)
    dest.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    return {
        "path": str(dest),
        "turns": len(TURNS),
        "chars": chars,
        "speakers": ["내비온 김형일", "KIST 김종민"],
    }


def build(out_dir: Path) -> dict:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        rows = list_voices()
        ko = _pick_korean_index(rows)
        info = {
            "voices": rows,
            "korean_voice_index": ko,
            "korean_voice_present": ko is not None,
        }
        if ko is None:
            info["error"] = ("SAPI 한국어 음성이 없습니다. 영어 음성으로라도 체인을 "
                             "검증하려면 --force-english 를 쓰세요.")
            return info

        tmp = out_dir / "_parts"
        tmp.mkdir(parents=True, exist_ok=True)
        part_paths = []
        for i, (spk, text) in enumerate(TURNS, start=1):
            part = tmp / f"{i:02d}_{spk}.wav"
            if spk == "A":
                speak_to_wav(text, part, ko, rate=0, pitch=0)
            else:
                # 동일 Heami 성문이므로 Rate·Pitch 로 구분 (한국어 2번째 음성 없음)
                speak_to_wav(text, part, ko, rate=-3, pitch=-8)
            part_paths.append(part)

        wav = concat_wavs(part_paths, out_dir / "meeting_2spk.wav")
        script = write_script(out_dir / "meeting_2spk.script.txt")
        meta = {
            "ok": True,
            "wav": wav,
            "script": script,
            "voices": rows,
            "korean_voice": rows[ko]["desc"],
            "speaker_b_style": {"rate": -3, "pitch": -8,
                                "note": "한국어 SAPI 음성이 Heami 1명뿐이라 피치·속도로 구분"},
            "english_terms": ["LiDAR", "TRL", "R&D", "KPI", "Kickoff", "PoC",
                              "MOU", "NDA", "SLA", "SNR", "GPU", "Edge TPU",
                              "real-time inference", "point cloud",
                              "Hyundai Motor NAMYANG R&D Center",
                              "detection range", "frame rate",
                              "technical review", "Zoom", "Teams",
                              "off the record"],
        }
        (out_dir / "meeting_2spk.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta
    finally:
        pythoncom.CoUninitialize()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        meta = build(out_dir)
    except Exception as e:
        print(f"실패: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)
    if not meta.get("ok"):
        sys.exit(2)
    dur = meta["wav"]["duration_sec"]
    print(f"\n길이 {dur:.1f}초 ({dur/60:.2f}분)  script={meta['script']['path']}",
          flush=True)
    if dur < 180:
        print("경고: 3분(180초)보다 짧습니다. 대본을 늘려야 합니다.", flush=True)


if __name__ == "__main__":
    main()
