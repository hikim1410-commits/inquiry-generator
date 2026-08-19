# STT 파일럿 실측 결과 (합성 2화자)

측정일: 2026-08-19
대상: `faster-whisper` + `sherpa-onnx` 로컬 스택 (PRD §10 Q5~Q7·Q9)
입력: Windows SAPI로 만든 합성 한국어 회의 wav (실제 내비온 녹음 **없음**)

재현:

```
python scripts/stt_selftest.py
python -m venv .venv-stt
.venv-stt\Scripts\pip install faster-whisper sherpa-onnx
.venv-stt\Scripts\python scripts/stt_pilot.py .stt-pilot-cache\selftest\meeting_2spk.wav --model small
.venv-stt\Scripts\python scripts/stt_pilot.py .stt-pilot-cache\selftest\meeting_2spk.wav --model medium
```

원본 로그·전사: `.stt-pilot-cache/selftest/meeting_2spk.{small,medium}.pilot.txt` (및 `.pilot.json`)

---

## 1. 측정 환경

| 항목 | 값 |
|---|---|
| CPU | 13th Gen Intel(R) Core(TM) i7-13700K |
| OS | Windows 11 (10.0.26200) |
| Python | 3.13.14 (`.venv-stt`) |
| faster-whisper | 1.2.1 |
| sherpa-onnx | 1.13.6 |
| 장치 / compute | CPU, `int8` |
| 언어 옵션 | 자동 감지 (`--lang` 미지정) |
| 화자 수 옵션 | `num_speakers=-1` (자동), `cluster_threshold=0.9` |

GPU는 쓰지 않았다.

---

## 2. 합성음 조건

SAPI 음성 목록(이 PC 실측):

| index | 이름 | Language |
|---|---|---|
| 0 | Microsoft Heami Desktop - Korean | 412 (ko-KR) |
| 1 | Microsoft Zira Desktop - English (United States) | 409 (en-US) |

한국어 음성은 **Heami 1명뿐**이다. 화자 A는 Rate 0 / Pitch 0, 화자 B는 같은 Heami에 Rate −3 / Pitch −8. 영어 음성 Zira는 한국어 대본에 쓰지 않았다(한영 혼용 시험이 영어 TTS로 오염되는 것을 피하기 위함).

| 항목 | 값 |
|---|---|
| 파일 | `.stt-pilot-cache/selftest/meeting_2spk.wav` |
| 포맷 | 16 kHz 16-bit mono |
| 길이 | **348.2초 (5.80분)** — 목표 3~5분을 턴 사이 무음 때문에 소폭 상회 |
| 턴 수 | 20 (A/B 교대) |
| 대본 글자 수 | 1,731자 (화자 태그 제외 발화만) |
| 생성기 | `scripts/stt_selftest.py` |

이 음원은 **동일 성문에 피치·속도만 다른 TTS**다. 화자분리 엔진이 실제 두 사람을 가르는지는 이 파일로 판정할 수 없다. 아래 화자분리 수치는 그 한계를 전제로 읽어야 한다.

한영 혼용 시험도 TTS가 영어 약어를 한국어 음소로 읽어 버린다. Whisper가 듣는 것은 “사람이 코드스위칭한 영어”가 아니라 “Heami가 발음한 영어”다. Q5 결과는 그 한계를 명시한다.

---

## 3. 대본 (정답지)

화자 A = 내비온 김형일, 화자 B = KIST 김종민.

1. A: 안녕하십니까. 주식회사 내비온의 김형일입니다. 오늘은 이천이십육년 팔월 십구일, 저가의 고효율 라이다 센서 사업 타당성 분석 용역 착수 회의를 시작하겠습니다.
2. B: KIST 광기술연구소 김종민입니다. 오늘 안건은 LiDAR 센서의 TRL 수준과 R&D 일정을 확정하는 것입니다. KPI 초안도 같이 보겠습니다.
3. A: 네. 본 용역 계약 금액은 일억 이천오백만 원이고, 수행 기간은 이천이십육년 구월 일일부터 이천이십칠년 이월 이십팔일까지입니다. Kickoff 는 구월 십오일 오후에 잡았습니다.
4. B: TRL 기준으로 보면 현재 시제품은 level 5 정도입니다. 연말 PoC 를 통과하면 level 6 으로 올릴 수 있습니다. 다만 outdoor 환경의 SNR 이 아직 부족합니다.
5. A: 정부 R&D 과제 협약서 상의 마일스톤은 십이월 중간점검, 이천이십칠년 일월 최종 보고입니다. 중간점검 때 MOU 초안도 제출해야 합니다.
6. B: MOU 상대는 현대차 남양연구소입니다. 영문 명칭은 Hyundai Motor NAMYANG R&D Center 입니다. NDA 는 이미 체결했고, SLA 초안은 아직입니다.
7. A: 알겠습니다. 인건비 구성을 말씀드리면 책임연구원 일 명, 연구원 이 명, 연구보조원 일 명입니다. 책임연구원 단가는 월 칠백오십육만 칠천사백오십육 원입니다.
8. B: 그 단가는 학술연구용역 인건비 기준에 맞습니다. 다만 KPI 의 정량 지표를 더 분명하게 써야 합니다. 예를 들어 detection range 백오십 미터, frame rate 십 헤르츠, 단가 목표 대당 팔십만 원입니다.
9. A: 네, KPI 표에 반영하겠습니다. 영어 약어는 회의록에 그대로 남기겠습니다. R&D, MOU, KPI, PoC, LiDAR, TRL, SLA, NDA, SNR 입니다.
10. B: 좋습니다. 기술 리스크는 두 가지입니다. 첫째, 눈과 비에서 point cloud 가 깨집니다. 둘째, GPU 없이 임베디드에서 real-time inference 가 빠듯합니다. Edge TPU 대안을 검토해야 합니다.
11. A: 예산 여유는 약 팔백만 원입니다. 시제품 추가 제작에 쓸 수 있습니다. 구매는 십일월 십일까지 발주해야 연내 입고가 됩니다.
12. B: 일정에 동의합니다. 다음 주 화요일 오전 열 시에 technical review 를 합시다. 자료는 월요일까지 공유 드라이브에 올려 주세요. 파일명은 Navion_LiDAR_R&D_v0.3.pptx 로 통일합시다.
13. A: 회의록 참석자는 내비온 김형일, 내비온 조명한, KIST 김종민, KIST 이민아 네 명입니다. 오늘은 두 분만 참석하셨습니다.
14. B: 액션 아이템을 정리합니다. 하나, 내비온은 KPI 표를 수정한다. 둘, KIST 는 TRL 증빙 자료를 보낸다. 셋, 양측은 MOU 영문 드래프트를 구월 오일까지 교환한다.
15. A: 추가합니다. 네, 중간점검 발표 자료는 십일월 이십일까지 초안을 돌린다. 다섯, 보안 때문에 원본 녹음은 외부 STT 클라우드로 보내지 않는다. 로컬 전사만 사용한다.
16. B: 동의합니다. 대외비 단가와 인건비가 오가는 회의입니다. off the record 로 남기지 말고, 회의록 확정 후에만 배포합시다.
17. A: 마지막으로 다음 회의는 이천이십육년 구월 오일 오후 두 시, 내비온 삼호빌딩 사층 회의실입니다. 온라인 병행은 Zoom 이 아니라 사내 Teams 를 씁니다.
18. B: 확인했습니다. 오늘 논의한 핵심은 LiDAR R&D 의 TRL 오에서 육, PoC 연말, MOU 는 현대차 남양연구소, KPI 는 range 와 단가입니다. 이상입니다.
19. A: 네, 이상으로 착수 회의를 마치겠습니다. 수고하셨습니다.
20. B: 수고하셨습니다. 자료는 오늘 저녁까지 올리겠습니다.

---

## 4. Q5 — 한영 혼용

자동 감지 언어: small `ko` (확률 0.986), medium `ko` (확률 0.997). `--lang ko` 강제 실험은 **미측정**.

회의 줄거리(용역 착수, 금액·기간, KPI, 보안)는 두 모델 모두 읽으면 복원된다. 영어 약어·고유명사는 자주 붕괴한다.

| 대본 | small 전사 | medium 전사 |
|---|---|---|
| LiDAR | 리디에이아어 / RIDIAO | 리디에이아어 / RIDIA |
| R&D | `R&D` (유지) / 알랜드 디 | `R&D` (유지) |
| Kickoff | 키코프 | 키코프 |
| MOU | 마우 | 마우 |
| KPI | `KPI` (유지) | `KPI` (유지) |
| PoC | PoC / POC | POC |
| TRL | `TRL` (유지) | `TRL` (유지, 한 소절에서 단어 누락) |
| SLA | 슬라 | 슬라 |
| NDA | `NDA` (유지) | `NDA` (유지) |
| SNR | SNI / SNR | 에센아어 / SNR |
| GPU | `GPU` (유지) | `GPU` (유지) |
| Edge TPU | LG TPU | 엘지 TPU |
| Zoom | 중 | 줌 |
| Teams | 팀즈 | 팀즈 |
| KIST | 기스트 / 키스트 | 기스트 / 키스트 |
| Hyundai Motor NAMYANG R&D Center | 현대이 모터 너 마이 앵 R&D 센터 | 헌데이 모터 너 마이 앵 R&D 센터 |
| technical review | 테니켈 리뷰 | 택리켈 리뷰 |
| off the record | 오프터 레커드 | 오프터 레커드 |
| point cloud | 포인트 클라우드 | 포인트 클라우드 |
| Navion_LiDAR_R&D_v0.3.pptx | NABION, RIDIAO, R&D TV 0.3.PPTX | navionridia r&tv 0.3.pptx |

한국어 고유명사·형태 오류 인용:

- 김형일 → small `김영일`. medium 첫 줄은 `김형일`, 참석자 줄은 `김영일`.
- 저가의 → small `적가의`. medium `저가의`.
- 착수 회의 → small `착소 회의`. medium `착수 회의`.
- 내비온 → `네비온` 혼용.
- KIST 광기술연구소 → `기스트 광기 수련구소`.
- 학술연구용역 → `학수련구용역`.
- 대외비 → small `대회비`. medium `대외비`와 `인권비`(인건비)가 섞임.
- 발주해야 → `8주해야`.
- 삼호빌딩 → `3호 빌딩`.

숫자·날짜는 대본의 한글 수사를 아라비아 숫자로 바꿔 맞춘 경우가 많다 (예: `2026년 8월 19일`, `1억 2500만 원`, `월 756만 7456원`). 이 부분은 두 모델 모두 강했다.

**Q5 판정:** 합성음 기준으로는 “영어 약어가 통째로 사라지지는 않지만, 회의록에 바로 쓸 만큼 안정적이지 않다.” 실제 사람이 코드스위칭한 녹음은 **미측정**.

---

## 5. Q6 — 분당 글자 수

대본 1,731자 / 5.803분 = **298.3자/분** (TTS 원문).

| 모델 | 전사 글자 수 | 분당 글자 수 |
|---|---|---|
| small | 1,589 | **273.8** |
| medium | 1,573 | **271.1** |

PRD가 가정한 한국어 발화 300~400자/분보다 조금 낮다. **SAPI 합성 + 턴 사이 무음** 기준이며, 사람 회의 녹음 분당 글자 수는 **미측정**.

---

## 6. Q7 — 오디오 1분당 처리 시간

처리 시간 = 전사초 + 화자분리초 (디코딩 0.1초는 무시할 수준). 모델 최초 다운로드 시간은 제외(아래 수치는 로드 후 해당 파일 처리).

| 모델 | 전사초 | 화자분리초 | 합계 | 오디오 1분당 | 실시간 배속 |
|---|---|---|---|---|---|
| small | 88.5 | 117.6 | 206.1 | **35.5초** | 1.69× |
| medium | 215.8 | 124.1 | 339.9 | **58.6초** | 1.02× |

60분 녹음으로의 **비례 환산**(이 5.80분 실측 × 60/5.80, 60분 파일 자체는 돌리지 않음):

| 모델 | 60분 환산 처리 시간 |
|---|---|
| small | 약 35.5분 |
| medium | 약 58.6분 |

60분 실파일 측정은 **미측정**. medium은 이 CPU에서 거의 실시간(1.02×)이라 1~2시간 회의면 체감 대기가 길다.

---

## 7. Q9 — 설치·모델 용량

실측 바이트(2026-08-19, 이 기계).

| 구성 | 경로 | 용량 |
|---|---|---|
| 파일럿 venv | `.venv-stt/` | 339.7 MB (339,748,727 B) |
| sherpa 세그멘테이션 (풀린 폴더) | `.stt-pilot-cache/sherpa-onnx-pyannote-segmentation-3-0/` | 7.6 MB |
| sherpa 임베딩 onnx | `.stt-pilot-cache/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` | 39.6 MB |
| faster-whisper small | `%USERPROFILE%\.cache\huggingface\hub\models--Systran--faster-whisper-small` | 972.4 MB |
| faster-whisper medium | `%USERPROFILE%\.cache\huggingface\hub\models--Systran--faster-whisper-medium` | 3,061.1 MB |

합계 (venv + sherpa 모델 47.2 MB + whisper, tar·합성 wav 제외):

| 채택 모델 | 합계 |
|---|---|
| small 스택 | **1,359.3 MB** |
| medium 스택 | **3,448.0 MB** |

모델 동봉은 이 숫자만으로도 기각된다. 런타임 다운로드가 맞다.

`.stt-pilot-cache/` 전체 76.1 MB에는 합성 wav·부분 wav(21.9 MB)와 tar.bz2 원본(7.0 MB)이 포함돼 있어, Q9 합계에는 넣지 않았다.

---

## 8. 화자 분리 (추가 항목)

`num_speakers=-1` 결과: 두 모델 모두 **추정 화자 수 2**, id `{0, 3}`.

실체는 거의 한 명이다.

| 모델 | turns 화자0 | turns 화자3 | 전사 세그먼트에 붙은 화자 |
|---|---|---|---|
| small | 74 | 1 (약 0.4초) | 거의 전부 화자0 |
| medium | 74 | 1 | **전부 화자0** (47/47) |

**2명을 2명으로 맞혔는가?** 클러스터 개수는 2로 나왔지만, 두 번째 화자는 0.4초 잡음 조각이고 발화 귀속은 실패했다. 동일 Heami 성문 TTS로는 **실용 화자분리 검증 불가**. 실제 2인 녹음 결과는 **미측정**.

---

## 9. 판정

**이 합성음만으로 파일럿 게이트를 통과시키지 않는다.**

1. **전사 엔진은 후보로 유지.** 숫자·날짜·용역 줄거리는 복원 가능하고, medium이 small보다 한국어 형태(저가의/착수/김형일 첫 소절)가 조금 낫다. 영어 약어·기관명·파일명은 회의록에 바로 넣기 어렵다.
2. **한영 혼용(Q5)은 미해소.** TTS가 영어를 한국어로 읽어 시험이 왜곡됐다. 실제 녹음이 필요하다.
3. **화자분리는 미해소.** 클러스터 수 2는 착시에 가깝다. 실제 두 목소리 녹음 전에는 통과/실패를 말하지 않는다.
4. **속도·용량은 동봉 불가, 런타임 설치가 맞다.** small은 이 PC에서 1분당 35.5초, medium은 58.6초. medium 스택은 약 3.4 GB.
5. **실제 내비온 회의 녹음 N건 측정이 남았다.** 그 전에는 전사본을 사용자 확인 없이 회의록에 자동 확정하면 안 된다.

권고(게이트 전 잠정): 코드 스택(faster-whisper + sherpa-onnx, 모델 런타임 다운로드)은 유지. 기본 모델 크기는 속도 때문에 small을 실사용 후보로 두고, 실제 녹음에서 medium 대비 고유명사 이득이 있으면 그때 올린다.

---

## 10. gitignore에 넣어야 할 경로

N2 담당. 이 파일럿이 만든 것:

- `.venv-stt/` — 엔진 venv (339.7 MB)
- `.stt-pilot-cache/` — sherpa 모델 + 합성 wav + 전사 산출물

저장소 밖(커밋 대상 아님):

- `%USERPROFILE%\.cache\huggingface\` — faster-whisper 가중치 (small 972 MB, medium 3.06 GB)

---

## 11. 미측정 (추정으로 채우지 않음)

- 실제 내비온 회의 녹음 (사람 발화, 실제 한영 코드스위칭, 실제 화자 성문)
- `--lang ko` 강제 vs 자동 감지 A/B
- 60분 파일 자체의 처리 시간 (5.80분 실측의 비례 환산만 있음)
- GPU / float16
- SenseVoice 등 폴백 스택 A/B
- Q8 (ONNX 재배포 라이선스) — 이 파일럿 범위 밖
