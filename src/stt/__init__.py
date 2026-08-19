# -*- coding: utf-8 -*-
"""음성 전사(STT) + 화자 분리 도메인 — 로컬 엔진 우선, API 키·네트워크 불필요.

설계 근거: `docs/PRD_회의록STT.md` v0.2 §7.1~§7.3.
확정 스택: faster-whisper(CTranslate2, PyTorch 불필요) + sherpa-onnx(ONNX Runtime).

이 파일은 **병렬 구현의 인터페이스 계약**이다. 각 모듈은 여기 선언된 이름과
시그니처를 그대로 구현해야 하고, 서로의 내부 구현에 의존하지 않는다.

무거운 선택적 의존성(faster_whisper·sherpa_onnx·av)은 **함수 안에서 지연 import**
한다 — 미설치 상태에서도 앱 부팅과 나머지 기능이 정상 동작해야 한다
(google-api-python-client를 선택적으로 다루는 기존 관행과 동일).

--------------------------------------------------------------------------------
세그먼트 스키마 (모듈 간 공통 자료형 — 이 형태를 벗어나지 말 것)

    Segment = {
        "start": float,      # 초. 오디오 시작 기준
        "end": float,        # 초
        "text": str,         # 발화 텍스트 (전사 결과)
        "speaker": str,      # "화자1" 등. 화자 분리 전에는 빈 문자열
    }

    Turn = {                 # diarize 가 내는 화자 구간 (텍스트 없음)
        "start": float,
        "end": float,
        "speaker": str,
    }

모든 시각은 **초(float)** 로 다룬다. 밀리초·문자열 시각은 UI 표시 직전에만 만든다.
--------------------------------------------------------------------------------

각 모듈이 제공해야 하는 공개 함수

    audio.py
        normalize(path) -> dict
            {"ok", "wav_path", "duration_sec", "error"}
            임의 오디오/영상 → wav 16kHz mono 로 표준화. PyAV 사용(ffmpeg.exe 불필요).
        probe(path) -> dict
            {"ok", "duration_sec", "has_audio", "error"}   디코딩 없이 메타만.
        chunk(wav_path, max_sec=1800) -> list[dict]
            [{"path", "offset_sec"}]  긴 오디오 분할. 짧으면 원본 1개를 그대로 반환.

    engine.py
        available() -> bool                     faster-whisper 임포트 가능 여부
        transcribe(path, opts=None, on_progress=None, should_cancel=None) -> dict
            {"ok", "segments": [Segment], "language", "duration_sec", "error"}
            opts: {"model": "medium", "language": "" , "compute_type": "int8"}
            on_progress(done_sec, total_sec) / should_cancel() -> bool 은 선택.

    diarize.py
        available() -> bool                     sherpa-onnx 임포트 가능 여부
        diarize(wav_path, num_speakers=-1) -> dict
            {"ok", "turns": [Turn], "num_speakers", "error"}
        assign(segments, turns) -> list[Segment]
            전사 세그먼트에 화자 라벨 부여(시간 겹침 최대 화자). 순수 함수 — 테스트 필수.
        merge(segments) -> list[Segment]
            같은 화자의 연속 세그먼트 병합. 순수 함수.

    runtime.py
        status() -> dict
            {"ok", "engine_installed", "diarize_installed", "models": {...},
             "total_bytes", "error"}
        ensure_models(on_progress=None) -> dict
            {"ok", "downloaded": [...], "error"}   화자분리 모델 GitHub Releases 내려받기.
        model_dir() -> str

    serialize.py
        to_transcript_json(segments, meta) -> dict         .transcript.json 본문
        to_plain_text(segments) -> str                     "[mm:ss] 화자1: …" 사람용
        to_minutes_input(segments, speaker_names=None) -> str
            기존 minutes_draft 의 description 으로 넣을 텍스트.
        apply_speaker_names(segments, mapping) -> list[Segment]
            {"화자1": "내비온 김형일"} 매핑 일괄 반영. 순수 함수.
        format_ts(sec) -> str                              12.3 -> "00:12"

--------------------------------------------------------------------------------
이 패키지는 하위 모듈을 즉시 import 하지 않는다. 사용처에서
`from src.stt.audio import normalize` 처럼 모듈 경로로 직접 가져간다
(선택적 의존성이 없는 환경에서 `import src.stt` 만으로 실패하지 않게 하기 위함).
"""

__all__ = ["audio", "engine", "diarize", "runtime", "serialize"]
