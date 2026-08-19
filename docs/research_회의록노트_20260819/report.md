# 회의록 노트 — 모듈 분해와 모듈별 오픈소스 탐색 보고

작성일: 2026-08-19
대상 문서: `docs/PRD_회의록노트.md` v0.1
조사 방식: 모듈별 병렬 탐색 6건(GitHub API·공식 문서·소스 직접 확인) + 로컬 실측 3건
선행 문서: `docs/PRD_회의록STT.md` v0.2, `docs/research_회의록STT_20260819/report.md`

---

## 0. 한 줄 결론

**Phase 1·2 전체가 신규 파이썬 의존성 0으로 구현 가능하다.** 다만 PRD v0.1의 세 곳(청킹 목적, 오디오 전달 방식, 검색 인덱스 옵션)은 조사 결과에 맞춰 고쳐야 한다.

---

## 1. 모듈 명세

| ID | 모듈 | 책임 | 신규 의존성 | 외부 조사 | 판정 |
|---|---|---|---|---|---|
| M1 | `src/ai/note.py` | `NOTE_SUMMARY_SCHEMA` 정의 · 프롬프트 · 응답 정규화 | 없음 | O | 자체 설계 + 프롬프트 패턴 2건 차용 |
| M2 | `src/note/summarize.py` | 요약 실행 · (필요 시) 구간 분할 · 병합 | 없음 | O | ownscribe 알고리즘 이식 |
| M3 | `src/note/search.py` | FTS5 인덱스 구축 · 질의 · 스니펫 | 없음 | O | trigram + LIKE 폴백 확정 |
| M4 | `ui/` 재생 싱크 | 전사문↔오디오 양방향 하이라이트 | 없음 | O | 라이브러리 미사용, 자체 구현 |
| M5 | (설계 검증) | 저장 구조 · 파이프라인 단계 · 보관 정책 | — | O | 사이드카 설계 유효 확인 |
| M6 | `src/note/serve.py` (신규) | 오디오를 webview에 전달 | 없음(bottle 재사용) | O | 자체 bottle 라우트 |
| M7 | `src/note/store.py` | `.note.json` 읽기·쓰기·마이그레이션 | 없음 | X | `src/store/minutes_store.py` 선례 |
| M8 | `src/note/serialize.py` | 요약 → `minutes_draft` 입력 텍스트 | 없음 | X | 자체 포맷(§6.4) |
| M9 | `src/api/note.py` | js_api 엔드포인트 | 없음 | X | `src/api/receipt.py` 선례 |

M7·M8·M9는 사내에 동형 선례가 있어 외부 조사를 하지 않았다. 베낄 코드가 이미 저장소 안에 있다.

---

## 2. 모듈별 조사 결과

### M1 — 요약 스키마·프롬프트 (`src/ai/note.py`)

| 저장소 | 스타 | 라이선스 | 최종 활동 | 판정 | 가져올 것 |
|---|---|---|---|---|---|
| [Zackriya-Solutions/meetily](https://github.com/Zackriya-Solutions/meetily) | 29,499 | MIT | 2026-06-05 | **참고** | 청크별 JSON 스키마 강제 + "해당 내용 없으면 빈 배열" 프롬프트 문구 |
| [silverstein/minutes](https://github.com/silverstein/minutes) | 1,439 | MIT | 2026-08-19 | **참고** | 프롬프트 인젝션 방어 문구 · "본문은 원문 언어, 필드 키는 영어 고정" |
| [github/awesome-copilot](https://github.com/github/awesome-copilot) `skills/meeting-minutes` | 38,004 | MIT | 활발 | 참고(약) | 액션아이템 필드 구성(`Action / Owner / Due`) |
| [bakaburg1/minutemaker](https://github.com/bakaburg1/minutemaker) | 21 | **없음(NOASSERTION)** | 2026-01-25 | **배제** | 개념만 — rolling 청킹, 상대시각→시계시각 환산 |
| [inboxpraveen/LLM-Minutes-of-Meeting](https://github.com/inboxpraveen/LLM-Minutes-of-Meeting) | 175 | MIT | 2026-04-14 | **배제** | 고유 기법 확인 못 함 |

**이 영역의 핵심 발견**: 조사한 어느 저장소에도 **"요약 항목 → 원문 t_ms"를 구조적으로 강제하는 구현이 없다.** 관찰된 유일한 패턴은 전사본에 `[시각] 화자:` 를 인라인으로 박아 LLM에 넣고 모델이 선택적으로 인용하게 두는 느슨한 방식이다. **우리가 직접 설계해야 하며, 동시에 이것이 우리 차별점이다.**

**채택 금지**: meetily의 `english_normalization_system_prompt` — 비영어 입력을 영어로 정규화한다. 한국어 원문 보존과 반대 방향.

**직접 설계할 것**
1. 입력 전사본을 `[t_ms] 화자: 발화` 형태로 세그먼트 인덱싱해 주고, "각 항목의 t_ms는 근거 세그먼트의 값을 **그대로 복사**하라, 새로 만들지 마라"를 스키마 required + few-shot으로 강제
2. `owner`/`due` 미언급 시 공란 유지 — 참고 저장소들은 "if mentioned" 수준으로 느슨하다. 우리는 **위반 예시(negative few-shot)** 까지 넣어 못박는다

### M2 — 긴 전사본 처리 (`src/note/summarize.py`)

| 저장소·자료 | 스타 | 라이선스 | 최종 활동 | 판정 | 가져올 것 |
|---|---|---|---|---|---|
| [paberr/ownscribe](https://github.com/paberr/ownscribe) `summarization/chunking.py` | 95 | MIT | 2026-08-14 | **채택** | 줄→문장→단어 순 분할, tail-overlap 5%, 재귀적 pairwise reduce. 순수 파이썬 의존성 0 |
| [KyiMoeTun/Transcript-Summarizer](https://github.com/KyiMoeTun/Transcript-Summarizer-Teams-Zoom) | 31 | MIT | 2023-05 | **배제** | 반면교사 — "화자 경계 분할" 표방하나 실제로는 1300단어 고정 절단 |
| [arXiv 2108.09597](https://arxiv.org/pdf/2108.09597) 계층적 요약 | — | — | — | 참고 | 한 화자가 장시간 발화하는 구간은 의미 단위 재분할 필요 |

**전제 정정 — 청킹은 컨텍스트 상한 회피 목적으로는 불필요하다.**

- 60분 전사본 3~5만 자 → 보수적 비율(1.3자/토큰)로 2.3만~3.9만 토큰
- Claude Opus 5/Sonnet 5 입력 1M 토큰, GPT-5.6 계열 1.05M — **상한의 4% 미만**
- Gemini는 공식 수치 재확인 실패(**미확인**), 다만 1M 미만일 가능성은 낮음

**청킹 모듈을 유지할 이유는 따로 있다**: ① 로컬 LLM(컨텍스트 8K급) 대응 여지, ② "lost in the middle"(긴 입력 중간부 정보 누락) 완화, ③ 비용·지연 관리. **설계 목적을 "상한 초과 방어"에서 "품질·호환성 안전장치"로 재정의한다.**

**ownscribe의 결함 — 우리가 메워야 할 것**: `speaker_text` 직렬화 과정에서 세그먼트의 `start/end` 타임스탬프를 버린다. 줄 앞에 `[HH:MM:SS] 화자:` 프리픽스를 박으면 줄 단위 분할 로직이 그대로 타임스탬프를 각 청크에 끌고 간다.

**토큰 추정**: 한국어는 토크나이저별 편차가 0.9~2.2자/토큰으로 크다. "글자수÷2" 어림은 최대 2배 이상 틀린다. 보수적으로 1.3~1.5자/토큰을 쓰고, 가능하면 프로바이더의 토큰 카운트 API로 검증한다. `tiktoken` 신규 도입은 명분 없음.

### M3 — 한국어 전문검색 (`src/note/search.py`)

| 자료 | 판정 | 확인된 사실 |
|---|---|---|
| [SQLite FTS5 공식 문서](https://www.sqlite.org/fts5.html) | **채택(레퍼런스)** | trigram은 "3 unicode characters 미만 부분열은 매치되지 않음"을 공식 명시. `detail=none`은 `highlight()`/`snippet()` 정상 동작을 깨뜨림 |
| [CPython `PCbuild/sqlite3.vcxproj`](https://github.com/python/cpython/blob/3.13/PCbuild/sqlite3.vcxproj) | **채택** | `SQLITE_ENABLE_FTS5`가 컴파일 플래그로 켜져 있음. 번들 SQLite: 3.11→3.45.1, 3.13→3.50.4. trigram은 3.34부터 코어 내장 |
| [zenn.dev CJK trigram 글](https://zenn.dev/kanseilink/articles/kanseilink-fts5-trigram-cjk-20260507?locale=en) | **채택(패턴)** | 2글자 이하 CJK 질의는 `LIKE '%…%'` 폴백으로 라우팅하는 하이브리드 |
| [andrewmara.com trigram 벤치마크](https://andrewmara.com/blog/faster-sqlite-like-queries-using-fts5-trigram-indexes/) | 참고 | 1,820만 행/1.3GiB 기준 인덱스 +2.4GiB(약 2.8배), LIKE 전체스캔 1.75초 → 10~30ms |
| [bab2min/kiwipiepy](https://github.com/bab2min/kiwipiepy) | **보류(2단계 후보)** | 398★, LGPLv3, 2026-08-17 활발. 휠 363KB이나 **사전 모델 88MB** |
| [lovit/soynlp](https://github.com/lovit/soynlp) | **배제** | 최종 커밋 2021-02-01, 5년 반 정체 |
| [hideaki-t/sqlite-fts-python](https://github.com/hideaki-t/sqlite-fts-python) | **배제** | 표준 `sqlite3`는 `sqlite3_fts5_api()` 포인터를 노출하지 않아 커스텀 토크나이저 등록 불가. APSW 도입이 전제 |

**결론: trigram 채택.** `unicode61` 단독은 "단가를"↔"단가" 불일치를 구조적으로 못 푼다. 형태소 분석기는 88MB 모델이 "의존성 0"과 충돌하므로 보류하되, trigram이 오히려 안전한 면이 있다 — 형태소 분석기는 사전에 없는 신조어·고유명사·오탈자에서 실패하지만 trigram은 글자 단위라 실패하지 않는다.

**설계 확정 사항**
- 2글자 이하 질의는 `LIKE` 폴백으로 라우팅 (trigram 인덱스가 LIKE도 가속함)
- **`detail=none` 사용 금지** — 검색 결과에 매칭 문맥 스니펫을 보여주기로 했으므로(PRD §5.3) 인덱스 크기보다 `snippet()` 동작이 우선

### M4 — 재생 싱크 UI

| 저장소 | 스타 | 라이선스 | 최종 활동 | 판정 |
|---|---|---|---|---|
| [samuelbradshaw/transcript-tracer-js](https://github.com/samuelbradshaw/transcript-tracer-js) | 23 | MIT | 2024-07-03 | 참고 — 단일 파일 순수 JS이나 O(n) 선형 탐색 |
| [guoyunhe/rabbit-lyrics](https://github.com/guoyunhe/rabbit-lyrics) | 173 | **GPL-3.0** | 2023-03-04 | **배제** — 카피레프트 오염 위험 |
| [podigee/podigee-podcast-player](https://github.com/podigee/podigee-podcast-player) | 212 | MIT | 2023-08-13 | 참고(기능 스펙만) — CoffeeScript+gulp 의존 |
| WebVTT `<track kind="metadata">` + `cuechange` | — | 표준 | — | **비권장** — STT 결과가 이미 JSON인데 VTT 왕복은 순손해 |
| CSS `content-visibility: auto` | — | 표준 | — | **채택** — 가상 스크롤 라이브러리 대체 |

**확인된 수치**: `timeupdate` 이벤트는 15~250ms 간격(초당 4~66회)으로 발생 → PRD 목표 ±0.5초를 네이티브 이벤트만으로 충족. requestAnimationFrame·별도 폴링 타이머는 과설계.

**결론: 라이브러리 미사용, 자체 구현.** 핵심은 이진탐색 + 클래스 토글이 전부다.

```
segments = [{start, end, speaker, text}, ...]     // STT JSON, 오름차순
starts   = segments.map(s => s.start)

audio.ontimeupdate = () => {
  const i = upperBound(starts, audio.currentTime) - 1     // O(log n)
  if (i !== active) { rows[active]?.classList.remove('on')
                      rows[i]?.classList.add('on')
                      rows[i]?.scrollIntoView({block:'nearest'})
                      active = i }
}
row.onclick = () => { audio.currentTime = segments[idx].start; audio.play() }
audio.playbackRate = 1.0 | 1.25 | 1.5 | 2.0       // 네이티브 속성
row css: content-visibility:auto; contain-intrinsic-size:auto 44px;
```

### M5 — 제품 구조 검증

| 제품 | 스타 | 라이선스 | 상태 | 판정 | 요지 |
|---|---|---|---|---|---|
| [Zackriya-Solutions/meetily](https://github.com/Zackriya-Solutions/meetily) | 29,499 | MIT | 활성 | 참고 | SQLite + ChromaDB 병행. **Python 백엔드 + 데스크톱 셸 이중 프로세스를 스스로 폐기**하고 단일 Tauri로 통합 |
| [thewh1teagle/vibe](https://github.com/thewh1teagle/vibe) | 7,135 | MIT | 활성 | 배제 | 전사 도구이지 노트 축적 제품이 아님 |
| [kaixxx/noScribe](https://github.com/kaixxx/noScribe) | 2,096 | **GPL-3.0** | 활성 | 부분 채택(아이디어만) | 완전 파일 기반, DB 없음. `Ctrl+Space`로 커서 위치 오디오 재생 |
| [reorproject/reor](https://github.com/reorproject/reor) | 8,572 | — | **아카이브(2025-05)** | **배제·반면교사** | 벡터DB+임베딩 자동연결 "제2의 뇌"를 노리다 유지보수 불가로 중단 |
| [tsheil/obsidian_plugin_AI_meeting_notes](https://github.com/tsheil/obsidian_plugin_AI_meeting_notes) | 6 | 0BSD/MIT | 소규모 | **참고(핵심)** | DB 없이 오디오·전사·노트 폴더 + front matter 경로 링크 — **우리 사이드카 설계의 실사용 선례** |
| [alexkroman/opennotes](https://github.com/alexkroman/opennotes) | 3 | MIT | **아카이브** | 배제 | ASR 3종+CLI+웹UI를 한꺼번에 벌이다 조기 중단 |

**검증된 것**
1. **파일 사이드카 저장이 실제로 성립한다** (tsheil). DB 미도입 결정 유지.
2. **근거 점프에 시맨틱 매칭은 불필요하다.** noScribe(`Ctrl+Space`)와 Obsidian `audio.mp3#t=754` 링크 모두 "세그먼트 시작 시각 저장 → 클릭 시 seek"로 끝냈다. 아무도 시맨틱 매칭을 하지 않았다.
3. **요약은 원문과 분리 저장하고 재생성 가능한 파생물로 취급** — 전 제품 공통 패턴. PRD와 일치.

**반면교사**
- Reor는 8.5k 스타에도 벡터DB 레이어를 감당 못 하고 죽었다. 우리는 지식관리 플랫폼이 아니라 기존 문서생성 앱의 부속 기능이므로 시맨틱 벡터 레이어는 범위 밖으로 확정한다.
- Meetily의 이중 프로세스 폐기는 우리 단일 프로세스 구조를 확인사살한다.

**선례가 없는 것 — Q1(오디오 보관 정책)**: 조사한 어떤 저장소도 보관·자동삭제 정책을 문서화하지 않았다. **오픈소스 생태계 전체가 아직 안 푼 문제이므로 베낄 데가 없다.** 우리가 직접 정하되, "선례 없음" 자체를 리스크로 기록한다.

### M6 — 오디오 전달 (`src/note/serve.py`)

| 방식 | 동작 | seek(Range) | 판정 |
|---|---|---|---|
| A. `file:///` 직접 참조 | **조건부/불안정** | 미확인 | **배제** |
| B. base64 data URI | 이론상 가능 | 불가(전체 메모리 적재) | **배제** |
| C. pywebview 내장 `http_server=True` | 동작 | **지원** | **조건부** — 서빙 범위 제약 |
| C′. 자체 bottle 라우트 | 동작 | **지원** | **채택** |
| D. WebView2 네이티브(`SetVirtualHostNameToFolderMapping` 등) | 가능 | 조건부 | **배제** — pywebview가 파이썬 레벨로 노출 안 함 |

**A 배제 근거**: pywebview 공식 아키텍처 문서가 *"its use is discouraged… not well supported"* 로 명시하고, 메인테이너 r0x0r 본인이 이슈 #1555에서 *"file:// protocol is flawed"* 라고 답했다. 같은 이슈에서 `--allow-file-access-from-files` 등 플래그를 다 시도하고도 `net::ERR_UNKNOWN_URL_SCHEME`로 실패한 사례가 있다.

**C의 제약(로컬 실측으로 확인)**: `webview/http.py` L98·L130 — `root_path = os.path.commonpath(local_urls)`이고 `bottle.static_file(file, root=server.root_path)`로만 서빙한다. 즉 **UI 엔트리포인트의 공통 상위 디렉터리 밖은 서빙되지 않는다.** 우리 배치(앱은 `C:\Program Files\…`, 작업 폴더는 사용자 지정 — 예 `F:\회의록`)에서는 드라이브가 달라 `commonpath`가 **`ValueError: Paths don't have the same drive`로 예외를 던진다.** 실측 확인.

**C′ 채택 — 새 의존성 없이 제약을 우회한다.**

`bottle`은 **이미 pywebview의 필수 의존성으로 설치되어 있다**(이 PC 실측: bottle 0.13.4). `bottle.static_file(filename, root=…)`의 `root`는 **호출 시점 인자**이므로, 우리가 라우트를 직접 만들면 요청마다 다른 폴더를 루트로 지정할 수 있다. `static_file`은 Range 헤더를 파싱해 206 + `Content-Range` + `Accept-Ranges`를 정식 반환하며(소스 실측: `parse_range_header`·`Content-Range`·`206` 모두 존재), 경로 탈출(`../`)도 자체 차단한다.

```
# 개념 — 노트 id → 폴더 매핑을 서버가 보유, 클라이언트는 절대경로를 모른다
@route('/audio/<nid>')
def audio(nid):
    folder, name = NOTE_AUDIO[nid]          # 화이트리스트. 임의 경로 노출 없음
    return bottle.static_file(name, root=folder)   # Range/206 자동 처리
```

Flask 도입(M6 대안안)은 불필요하다. 이미 있는 bottle로 같은 일이 된다.

**M6 미확인 항목**
- pywebview 내장 `http_server` + PyInstaller **onedir** 조합의 실동작 사례 (정황상 문제없을 것으로 추정, 실측 아님)
- `<audio>` + WebView2 + `file://` 조합의 seek 성공/실패 (찾은 사례는 전부 `<video>` 대용량)

---

## 3. 로컬 실측 (이 PC, 2026-08-19)

| # | 확인한 것 | 결과 |
|---|---|---|
| L1 | 표준 `sqlite3`의 FTS5·trigram 가용성 | 둘 다 가용. SQLite 3.50.4 |
| L2 | 한국어 재현율 `unicode61` vs `trigram` | `조건부`·`건부입`·`물량 5` 모두 기본은 누락, trigram은 적중. `단가는`은 양쪽 적중 |
| L3 | trigram 2글자 질의 | **양쪽 모두 누락**. 단 `LIKE '%단가%'`는 적중 → 폴백 경로 성립 |
| L4 | 인덱스 크기 배율 | 합성 데이터 기준 1.3배 (M3가 인용한 실환경 벤치마크는 2~3배. **실제 전사본으로 재측정 필요**) |
| L5 | `bottle.static_file` Range 지원 | `parse_range_header`·`Content-Range`·`Accept-Ranges`·`206` 모두 존재 |
| L6 | `os.path.commonpath` 교차 드라이브 | `ValueError: Paths don't have the same drive` — C의 제약이 우리 배치에서 실제로 깨짐 |

L4는 같은 문장을 반복한 합성 코퍼스라 압축이 과하게 먹었을 수 있다. **배율은 파일럿에서 실제 전사본으로 다시 잰다.**

---

## 4. PRD v0.1 개정 필요 항목

| 위치 | 현재 기술 | 고칠 내용 | 근거 |
|---|---|---|---|
| §7.4 | "LLM 입력 상한을 넘으면 구간 분할" | 목적을 **"로컬 모델 호환성 + lost-in-the-middle 완화"** 로 재정의. 상한 초과는 현실적으로 발생하지 않음 | M2 |
| §7.3 | `file://` 직접 참조부터 시험 | **C′(자체 bottle 라우트)로 확정.** A는 메인테이너가 flawed로 명시, C는 교차 드라이브에서 실측 실패 | M6, L6 |
| §7.2 | 토크나이저 미결(Q6) | **trigram 확정 + 2글자 이하 LIKE 폴백.** `detail=none` 사용 금지 명시 | M3, L1~L3 |
| §9 | — | 리스크 추가: **오디오 보관 정책에 오픈소스 선례가 없음** | M5 |
| §10 | Q2·Q6 미해결 | **Q2·Q6 해소 처리.** Q1은 "선례 없음"을 명시한 채 존치 | M3, M6 |
| §12.1 | 파일럿 항목 | `file://` 재생 검증 → **bottle 라우트 206 응답 검증**으로 교체 | M6 |

---

## 5. 파일럿에서 실측할 것 (개정판)

| # | 항목 | 판정 기준 |
|---|---|---|
| P1 | 실제 회의 전사본 → `NOTE_SUMMARY_SCHEMA` | 액션아이템 재현율 80%↑, 오탐 5%↓ |
| P2 | 근거 시각 정합 | 표본 20개 중 18개↑ 실제 구간 일치 |
| P3 | 60분 전사본 글자 수·토큰·요약 소요 | 수치 확정 (선행 문서 Q6과 공동 측정) |
| P4 | 자체 bottle 라우트 오디오 서빙 | DevTools Network에서 **206 Partial Content** 확인, seek·2배속 무결 |
| P5 | trigram 인덱스 실제 크기 배율 | 실제 전사본 코퍼스로 재측정 (L4 보정) |
| P6 | 2글자 LIKE 폴백 지연 | 1,000건 규모에서 300ms 이내 |
| P7 | 단건 노트 저장 시 인덱스 갱신 비용 | upsert 경로 지연 |

---

## 6. 사실대장

| ID | 사실 | 출처 |
|---|---|---|
| N001 | CPython Windows 공식 빌드는 `SQLITE_ENABLE_FTS5`를 컴파일 타임에 활성화 | `python/cpython` `PCbuild/sqlite3.vcxproj` |
| N002 | 번들 SQLite: Python 3.11→3.45.1, 3.13→3.50.4. trigram은 3.34부터 코어 내장 | `PCbuild/get_externals.bat` |
| N003 | FTS5 trigram은 3 유니코드 문자 미만 부분열을 매치하지 않음 | sqlite.org/fts5.html |
| N004 | `detail=none`은 `highlight()`/`snippet()` 정상 동작을 깨뜨림 | sqlite.org/fts5.html + 커뮤니티 보고 |
| N005 | 표준 `sqlite3`는 `sqlite3_fts5_api()` 포인터 미노출 → 커스텀 토크나이저 등록 불가 | hideaki-t/sqlite-fts-python 문서 |
| N006 | pywebview 공식 문서·메인테이너가 `file://` 사용을 비권장 | pywebview `docs/guide/architecture.md`, issue #1555 |
| N007 | pywebview 내장 http 서버는 `commonpath(local_urls)` 하위만 서빙 | `webview/http.py` L98·L130 |
| N008 | `bottle.static_file`은 Range → 206 Partial Content를 표준 지원 | bottle 0.13 changelog, issue #91, 로컬 소스 실측 |
| N009 | `timeupdate` 이벤트 발생 간격 15~250ms | MDN/WHATWG |
| N010 | Claude Opus 5/Sonnet 5 입력 1M 토큰, GPT-5.6 계열 1.05M | 각 공식 모델 문서 |
| N011 | 한국어 토크나이저별 비율 0.9~2.2자/토큰으로 편차 큼 | 공개 토크나이저 실측 자료 |
| N012 | kiwipiepy 휠 363KB, 사전 모델 88MB, LGPLv3 | PyPI JSON |
| N013 | 요약 항목에 원문 t_ms를 강제하는 오픈소스 구현은 확인되지 않음 | M1 탐색 결과 |
| N014 | 오디오 원본 보관 정책을 문서화한 오픈소스 노트테이커는 확인되지 않음 | M5 탐색 결과 |
