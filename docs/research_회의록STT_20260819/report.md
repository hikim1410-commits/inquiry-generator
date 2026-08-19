# 회의 녹음 전사(STT) + 화자 분리 — GitHub 탐색 보고

조사일: 2026-08-19 · 대상 PRD: `docs/PRD_회의록STT.md` v0.1의 `[확인 필요]` 해소
조사 방식: 저장소·공식 문서 직접 열람(WebFetch) + 웹 검색. **1차 출처(저장소/공식 문서)에서 확인한 것만 `확정`**, 2차 출처(블로그·비교글)는 `참고`로 구분한다.

---

## 0. 먼저 — 사용자가 언급한 "codex 쪽 음성엔진"에 대하여

OpenAI의 GitHub에 있는 음성 엔진은 **Codex가 아니라 `openai/whisper`** 다. Codex는 코딩 모델/CLI 계열이고 음성 기능이 없다. 회의록에 쓸 엔진은 Whisper 계열이 맞으며, 다만 **원본 `openai/whisper`(PyTorch 구현)를 그대로 쓰는 것은 데스크톱 배포에 부적합**하다 — PyTorch 런타임(수 GB)을 앱에 끌고 들어와야 하기 때문이다. 실제로 데스크톱 앱들은 전부 재구현판(whisper.cpp / faster-whisper)을 쓴다(F008·F009).

---

## 1. 사실대장

### 로컬 엔진

| ID | 사실 | 근거 | 등급 |
|---|---|---|---|
| F001 | **sherpa-onnx**(k2-fsa)는 Apache-2.0. STT·TTS·**화자 분리**·VAD를 ONNX Runtime으로 수행하며 **런타임에 PyTorch 불필요** | 저장소 열람 | 확정 |
| F002 | sherpa-onnx PyPI 휠은 **Windows x64 cp37~cp314 제공**, 휠 크기 **2.0~4.4MB**, 최신 1.13.6(2026-08-18), 의존성은 Python≥3.7뿐 | PyPI 열람 | 확정 |
| F003 | sherpa-onnx 화자분리 = **pyannote-segmentation-3.0 ONNX 변환본 + 3D-Speaker/NeMo 임베딩**, 모델은 **GitHub Releases에서 배포**(HF 게이트·토큰 불필요) | 공식 문서 열람 | 확정 |
| F004 | sherpa-onnx 화자분리는 `num_speakers=-1` + `cluster_threshold`로 **화자 수 자동 추정** 가능. 입력은 float32 배열(지정 샘플레이트), 출력은 `(start, end, speaker)` | `python-api-examples/offline-speaker-diarization.py` 열람 | 확정 |
| F005 | **pyannote/speaker-diarization-3.1**은 MIT 라이선스지만 **게이트 모델** — 사용 조건 수락 + Hugging Face 토큰 필요 | HF 모델카드 열람 | 확정 |
| F006 | **faster-whisper**(SYSTRAN)는 MIT. CTranslate2 기반으로 **PyTorch 불필요**, CPU int8 지원, **Silero VAD 내장**, 롱폼 전사 지원, **화자분리는 미포함**. 오디오 디코딩에 **PyAV 사용 → FFmpeg 별도 설치 불필요**. Python 3.9+ | 저장소 열람 | 확정 |
| F007 | **whisper.cpp**는 MIT, Silero VAD 지원. 내장 화자분리(tinydiarize)는 **실험적이며 `small.en` 전용(영어)** → 한국어 회의에 사용 불가 | 저장소 열람 | 확정 |
| F008 | sherpa-onnx는 **SenseVoice(zh·en·ja·ko·yue)** 와 **한국어 zipformer** 사전학습 모델을 제공 | 저장소 열람 | 확정 |
| F009 | **SenseVoice**는 코드 MIT / 가중치는 별도 모델 라이선스(상용 허용, 조건 준수 시). Korean 포함 5개 언어, Whisper-Large 대비 **15배 이상 빠름**, CTC 기반 타임스탬프 제공, ONNX 내보내기 지원 | 저장소 열람 | 확정 |
| F010 | 한국어 정확도는 **Whisper large-v3 우세**(CER 8~12%대), SenseVoice의 우위 언어는 중국어·광둥어이며 한국어는 포함되지 않음 | 2차 출처 다수 | 참고 |

### 유사 제품 (구조 참고)

| ID | 사실 | 근거 | 등급 |
|---|---|---|---|
| F011 | **noScribe** — Windows/mac/Linux 데스크톱, GPL-3.0, **faster-whisper + pyannote** 조합. **모델을 동봉해 배포 용량이 수 GB**. 화자 이름은 사용자가 편집기에서 찾아바꾸기로 수동 지정. 긴 파일에서 Whisper 반복 루프 발생 사례 명시 | 저장소 열람 | 확정 |
| F012 | **Vibe**(thewh1teagle) — MIT, Tauri+Rust, whisper.cpp 기반, **화자분리 지원**, 모델은 앱에서 다운로드·관리 | 저장소 열람 | 확정 |
| F013 | **Meetily** — MIT, 로컬 회의 어시스턴트. 저장소 소개문과 달리 **화자분리는 커뮤니티 에디션 미제공(PRO 예정)** | 2차 출처 | 참고 |

### 클라우드 경로

| ID | 사실 | 근거 | 등급 |
|---|---|---|---|
| F014 | **CLOVA Speech**(네이버클라우드)는 한국어 특화 STT로 화자 인식을 제공하고, **국내 개인정보보호 법령 준수를 명시**. 과금은 15초 단위로 알려짐(정확한 단가 미확인) | 2차 출처 + 제품 페이지 | 참고 |
| F015 | ElevenLabs Scribe·AssemblyAI의 한국어 화자분리 단가는 이번 조사에서 확인하지 못함 | — | 미확인 |

---

## 2. 판정 — PRD `[확인 필요]` 해소

### Q1. 전사 엔진 → **로컬 우선, faster-whisper**

근거: 회의 녹음의 대외비 민감도(§PRD 1.3)를 감안하면 로컬이 기본이어야 하고, 로컬 중에서는 한국어 정확도(F010)와 롱폼·VAD 내장(F006)이 결정적이다. `openai/whisper` 원본은 PyTorch 때문에 배제, whisper.cpp는 파이썬 앱에 붙이려면 별도 바인딩·바이너리 관리가 늘어난다(F007).

### Q2. 화자 분리 → **sherpa-onnx 오프라인 화자분리 (pyannote.audio 직접 사용은 배제)**

이것이 이번 조사의 **가장 중요한 발견**이다. pyannote.audio는 사실상 표준이지만 **게이트 모델 + HF 토큰**을 요구한다(F005). 일반 사무 사용자에게 "Hugging Face 계정을 만들고 모델 사용 조건에 동의한 뒤 토큰을 발급해 앱에 붙여넣으세요"를 시킬 수는 없다 — 기능 자체가 안 열린다. 반면 sherpa-onnx는 **같은 pyannote 세그멘테이션 모델의 ONNX 변환본을 GitHub Releases로 배포**하므로 토큰·계정이 필요 없고, 휠이 4MB에 PyTorch도 필요 없다(F001~F004). 화자 수 자동 추정도 API로 지원된다.

> 잔여 확인: ONNX 변환본 재배포의 라이선스 승계(원본 MIT) — 상용/사내 배포 전 모델카드 재확인. `[확인 필요]`

### Q3. 긴 전사본 투입 → **분량 상한 분리 + 구간 요약 폴백**

`MAX_ATTACH_TOTAL = 30_000`은 문서 첨부용 값이므로, 전사본에는 별도 상한을 둔다. 그래도 넘치면 앞부분을 자르는 현행 동작(뒷부분 = 결론·할 일이 날아감)을 **뒤집어**, 구간별 요약 후 투입한다.

### Q4. 오디오 디코딩 → **PyAV (FFmpeg 별도 동봉 불필요)**

faster-whisper가 이미 PyAV로 디코딩한다(F006). PyAV 휠은 FFmpeg 라이브러리를 포함하므로 m4a/aac를 별도 exe 없이 처리할 수 있다. → PRD §7.6의 "ffmpeg 동봉 수십 MB" 우려는 해소.

### Q5. 한영 코드스위칭 → **파일럿 필수 (미해소)**

Whisper는 세그먼트 단위로 언어를 추론하므로 한국어 회의 중 영어 문장이 섞이면 오역·누락 가능성이 있다. 언어를 `ko`로 고정할지 자동 감지로 둘지는 실제 녹음으로 판정한다. **PRD §8.1 게이트 항목으로 유지.**

### Q6·Q7. 분당 글자 수 / 60분 처리 시간 → **미해소, 실측 필요**

내비온 기존 전사본 txt와 실제 회의 녹음으로 측정한다.

---

## 3. 권고 스택

```
[m4a/mp4]
   │  PyAV 디코딩 → 16kHz mono float32          (Q4)
   ├─────────────────────────┬──────────────────────────
   ▼                         ▼
faster-whisper            sherpa-onnx
 (Silero VAD 내장)         offline speaker diarization
 → 세그먼트 + 타임스탬프    → (start, end, speaker)
   └──────────┬──────────────┘
              ▼  타임스탬프 겹침으로 세그먼트 ↔ 화자 결합
        [00:12] 화자1: … / [00:31] 화자2: …
              ▼
        화자 이름 매핑 → 회의록 AI 초안(기존) → HWPX(기존)
```

| 구성요소 | 선택 | 라이선스 | 배포 부담 |
|---|---|---|---|
| 오디오 디코딩 | PyAV | BSD 계열 | 휠에 FFmpeg 포함 |
| 전사 | faster-whisper (+CTranslate2) | MIT | 라이브러리 수십 MB + **모델 별도 다운로드** |
| 화자 분리 | sherpa-onnx | Apache-2.0 | **휠 4MB** + 모델 수~수십 MB |
| 결합·직렬화 | 자체 구현 | — | — |

**모델은 exe에 동봉하지 않는다.** kordoc 런타임 설치 선례(`src/convert/kordoc.py`)를 그대로 이식해 첫 사용 시 다운로드한다 — noScribe가 모델 동봉으로 수 GB 배포가 된 전철(F011)을 밟지 않기 위해서다.

### 대안(폴백) 스택 — 경량 단일 의존성

sherpa-onnx **단독**으로 VAD + ASR(SenseVoice 또는 한국어 zipformer) + 화자분리를 모두 처리하는 경로. 휠 4MB에 모든 것이 끝나지만, 한국어 정확도가 Whisper보다 낮을 가능성(F010)과 롱폼 조립을 직접 해야 하는 부담이 있다. **전사 엔진 인터페이스를 교체 가능하게 설계해 파일럿에서 A/B 비교한다.**

### 클라우드는 옵션으로만

CLOVA Speech는 한국어 특화 + 화자 인식 + 국내 법령 준수 명시로 매력적이지만(F014), 녹음 원본이 외부로 나간다. **기본값은 로컬, 사용자가 명시적으로 켜는 옵션**으로 둔다(PRD FR-11).

---

## 4. 다음 단계

1. **파일럿(착수 게이트)** — 내비온 실제 회의 녹음 5~10건으로 권고 스택 프로토타입 측정: 한국어 가독성, 한영 혼용(Q5), 화자 분리 유용성, 60분 처리 시간(Q7), 분당 글자 수(Q6).
2. 게이트 통과 시 PRD v0.3(엔진 확정본) 발행 후 `src/stt/` 구현 착수.
3. 착수 전 `MAX_ATTACH_TOTAL` 분리(Q3)를 선행 — 이것 없이는 FR-07을 구현하지 않는다.

---

## 5. 출처

- [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) · [sherpa-onnx PyPI](https://pypi.org/project/sherpa-onnx/) · [Speaker Diarization 문서](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html) · [offline-speaker-diarization.py](https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/offline-speaker-diarization.py)
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp)
- [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
- [FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice)
- [kaixxx/noScribe](https://github.com/kaixxx/noScribe) · [thewh1teagle/vibe](https://github.com/thewh1teagle/vibe) · [Zackriya-Solutions/meetily](https://github.com/Zackriya-Solutions/meetily)
- [CLOVA Speech Recognition](https://www.ncloud.com/product/aiService/csr)
- 2차 출처(참고 등급): [SenseVoice vs Whisper CJK 비교](https://whispernotes.app/blog/sensevoice-fastest-cjk-transcription), [오픈소스 STT 벤치마크 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks), [한국어 STT 비교](https://soniox.com/korea)
