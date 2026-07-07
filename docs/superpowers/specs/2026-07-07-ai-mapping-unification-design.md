# 설계: AI 매핑 통합 + 다중 표 완전 지원

- 날짜: 2026-07-07
- 상태: 사용자 승인됨 (설계안 기준)
- 대상 버전: v1.6.0 (기능 추가, 하위 호환)
- 배경: 회의록 매핑 편집기의 "✦ AI 표준 항목 매핑"과 "✨ AI 라벨 스캔"이 같은 격자 스캔 +
  같은 개념("라벨 칸 옆 입력 칸 찾기")을 버튼 2개·AI 호출 2회로 중복 수행. 병합 정책도
  상이(교체 vs 건너뜀). 또한 cell_map/custom_slots가 표0 전용이라 표1+ 핀이 생성물에
  반영되지 않음(UI는 경고만). 사용자 확정 요구: ① 통합 ② 인식 정확도 ③ 라벨 패턴
  다양화 ④ 다중 표 완전 지원.

## 1. 목표 / 비목표

**목표**
1. 버튼 1개("✨ AI 자동 매핑")·AI 호출 1회로 표준 7슬롯 + 커스텀 라벨 동시 인식.
2. 좌표 체계 `[table,row,col]` 3요소화 — 표1+의 표준 슬롯·커스텀 슬롯이 실제 생성물에 반영.
3. 인식 정확도 강화: 병합셀 정보 프롬프트 반영 + 코드 후검증(실존성·인접성·공백).
4. 라벨 패턴 커버리지: 오른쪽/아래 입력, 한 행 2쌍, 전폭 제목행 배제, 대형 병합 본문칸.

**비목표 (이번 범위 제외)**
- "라벨: 값" 같은-셀 채우기 — 감지 후 건너뛰기만 (쓰기 로직이 달라 후속 과제).
- 회의록 목록 화면(parse_minutes_hwpx)의 커스텀 좌표 메타 추출 — 기존 한계, 후속.
- 스플릿 버튼(부분 재실행)·상단 항목 그룹핑/스크롤 — 사용 증거 나오면 P2.
- OpenAI 프로바이더 schema 미전달(llm.py 기존 한계) — 코드 후검증으로 방어.
- 편집기 오픈 시 AI 자동 호출 — C-2 결정(명시적 버튼 클릭만) 유지.

## 2. AI 계층 — `map_minutes_form()` (src/ai/minutes_template_mapper.py)

기존 `map_minutes_cells` / `auto_label_cells`를 **대체**(테스트 이관 후 삭제).

```python
def map_minutes_form(grid_cells, provider="gemini", api_key="",
                     model="gemini-flash-latest", timeout=30) -> dict:
    # 반환: {"ok", "cell_map": {slot: [t,r,c]}, "unmapped": [slot],
    #        "pins": [{table,row,col,label}], "error"?}
```

**AI 와이어 스키마** (파이썬 반환값과 분리):
```json
{"slots": [{"slot":"business_name","table":0,"row":1,"col":1}, ...],
 "pins":  [{"table":0,"row":7,"col":1,"label":"작성자"}, ...]}
```
- `unmapped`는 AI에게 요청하지 않음 — MINUTES_SLOTS(7개) 중 slots에 없거나 검증 탈락분을
  코드가 계산 (slots/unmapped 모순 응답 원천 차단).
- v1은 `schema=None`(JSON 모드, 기존 패턴 유지). strict 스키마는 억지 채움 부작용 우려로
  보류 — 코드 후검증이 최종 방어선.

**그리드 직렬화** (표별 헤더 + 병합 표기, 실측 기준):
```
[표0] 7행×3열
  (0,0)+cs3: 회 의 록
  (1,0): 사업명
  (1,1)+cs2: 내용
  ...
```
- colspan/rowspan 1이면 태그 생략, 병합만 `+cs{n}`/`+rs{n}`.
- 프롬프트에 "병합으로 덮인 좌표는 목록에 없음 — 목록에 있는 좌표만 사용" 명시.
- 텍스트 120자 절단·`⏎` 줄바꿈은 기존 _extract_table_cells 그대로.
- 규모 실측: 표준 양식 전체 프롬프트 2,501자, 10표급 실무 양식 ~3,400자 추정 — 비용 문제 없음.

**프롬프트 구성** (AI팀 초안 채택):
- 개념 정의(라벨 칸/표준 슬롯/커스텀 항목/입력 칸) + 우선순위(오른쪽→아래, 병합 폭 감안).
- 작업 순서: ① 표준 7슬롯 먼저 → ② 그때 쓴 칸을 제외한 나머지 라벨만 pins (표준 우선, 중복 금지).
- 공백 요건 비대칭: 표준 슬롯 입력 칸은 견본 텍스트("(총 N명)") 허용, 커스텀 핀은 빈 셀만.
- 전폭 병합 셀(colspan=그 표 전체 열수)은 제목/구분선으로 배제.
- 한 행 2쌍(라벨-입력-라벨-입력) few-shot 예시 포함.
- "라벨:" 접두 + 인접 빈 칸 없음 → 건너뛰기 지시.

**코드 후검증 규칙** (프롬프트 신뢰하지 않음):
1. 좌표 실존성 — grid_cells 집합 대조. slots에도 적용(기존 map_minutes_cells에 없던 검증).
2. 인접성 역산 — AI에게 라벨 좌표를 묻지 않고, 선택된 입력 칸에서 `_neighbor_left/_neighbor_above`로
   왼쪽/위 비어있지 않은 셀 존재를 확인. `col+colspan`/`row+rowspan` 기반(병합 폭 정확 계산),
   **같은 table 부분집합에서만** 탐색(표 간 (row,col) 중복 흔함). 실패 시 완전 배제 대신 경고 유지.
3. 공백 비대칭 — pins는 빈 셀만, cell_map은 견본 텍스트 허용.
4. 중복 — slots 내부/pins 내부 첫 항목만, slots↔pins 교차 중복은 pins 폐기(표준 우선).
5. slot명 enum 밖이면 무시. 라벨 칸 자신 = 입력 칸 금지.

## 3. 엔진 계층 — build_minutes 다중 표 (src/minutes/hwpx_minutes.py)

**표 인덱싱 계약**: `_iter_top_tables`(중첩 표 제외 — 사진표가 표0 회의내용 셀 안에 중첩돼
있어 naive findall()[N]은 사진표를 표1로 오인)를 hwpx_scan.py → hwpx_minutes.py로 **이동**,
scan이 import (기존 관례: scan은 이미 _HP·_find_cell을 minutes에서 import). 스캔과 생성의
표 번호가 같은 함수 출력 — 정의상 불일치 불가. 순환 참조 없음.

**fieldmap v3**:
- 저장: version 3, cell_map/custom_slots 좌표 항상 `[table,row,col]` 3요소(표0도 [0,r,c]).
- 읽기: 좌표 길이로 분기(len>=3 → 그대로, 2 → table 0) — v1/v2 파일·기존 재편집 데이터
  무수정 영구 호환. 사용자가 매핑 저장하는 순간 자연 승격(v1→v2와 동일 지연 마이그레이션).
- `DEFAULT_CELLS` 3-tuple화 + **`is_standard_map` 3요소 비교로 동시 수정**(잊기 쉬운 회귀 포인트).

**build_minutes**:
- `tables = _iter_top_tables(root)`; `_resolve((t,r,c))` — 범위 밖 표는 경고+건너뛰기
  (기존 "미매핑=빈칸" 철학). 8개 채움 지점(표준 7 + custom_slots) 동일 패턴.
- content도 동일 지원 — 하위 함수(_find_cell, _fill_para_clone 등)는 표 번호 무관.
  사진표 없는 셀 매핑은 기존 T-A6-2 경고가 그대로 작동.
- **불변식**: subList의 p를 전부 remove하는 경로는 반환 전 최소 1개 p 보장
  (2026-07-07 한글 크래시 수정의 일반화) — 표1 대상 테스트로 재확인.

**커스텀 슬롯 ID**: 표0은 기존 `c{row}_{col}` 유지(기존 custom_fields 연결 보존),
표1+만 `c{table}_{row}_{col}` — 충돌 없음, 마이그레이션 제로.

**변경 불필요 확인됨**: src/api.py(얇은 통과 계층), src/store/minutes_store.py(좌표 무관),
캔버스/핀 렌더링 레이어(이미 table 인지).

## 4. UI 계층 (ui/app.js, ui/index.html, ui/app.css)

**버튼 통합**:
- 사이드바 `#mn-map-ai` 자리에 통합 버튼 "✨ AI 자동 매핑" 1개. 푸터 `#mn-map-autolabel`
  제거 + app.css의 `.modal-actions .mn-map-autolabel` 규칙 제거.
- 툴팁: "양식의 표준 7항목(사업명·일시 등)과 그 외 라벨 칸을 AI로 한 번에 찾아 핀을 배치합니다".
- AI 키 없으면 disabled + title 안내(라벨 스캔 쪽 기존 패턴으로 통일).
- 오버레이: "AI가 양식을 분석하는 중...".
- 결과: #mn-map-warn 배너 "AI 매핑 완료 — 표준 N개, 커스텀 M개 · 미매핑 K개 (...)" + 기존 toast.

**핀 병합 정책 단일화**: AI가 이번에 제안한 정확한 (table,row,col)만 교체, 그 외 기존 핀
전부 보존 — 기존 표준 매핑 로직(3요소 키, 검증됨) 재사용. 확인 모달 없음(저장 전까지
모달 내 임시 상태 — 기존 확인 모달 사용 기준과 일치). 스캔 결과 핀은 (table,row,col)
정렬 후 annotations에 삽입.

**표0 제한 잔재 정리** (다중 표 지원으로 사실이 아니게 된 것들):
| 위치 | 조치 |
|---|---|
| app.js:1950 `normCellMap` | **3요소 절단 버그 수정** — len>=3 유지, len==2 → [0,r,c] 승격 |
| app.js:2116 `mnDeriveCellMap` | 표0 필터 제거, `[mnT(a),row,col]` 3요소 반환 |
| app.js:2125 `mnPinsOffTable0` | 함수 삭제 |
| app.js:2134 `mnDeriveCustomSlots` | 필터 제거 + ID 규칙(§3) 적용 |
| app.js:2198 `mnCellMapToAnns` | 3요소 대응 (`table: rc[0]`) |
| app.js:2337 savePinPop 표0 경고 | 삭제 |
| app.js:2459 저장 시 mnPinsOffTable0 경고 | 삭제 |
- 죽은 코드 `scanMinutesTemplate()`(app.js:1461~) + 관련 마크업(#mn-tpl-scan-result 등) 제거.

**백엔드 엔드포인트**: `scan_minutes_template`/`auto_label_minutes_form` → 통합 엔드포인트
`Api.map_minutes_form(template_path)` 1개로 교체(fieldmap 캐시 저장·AI 실패 시 기존 캐시
보호 규칙은 scan_minutes_template의 현행 동작 승계, 반환에 grid 포함 — UI가 핀 위치를
mnPinPos로 계산). `scan_minutes_grid`(오프라인 격자)는 그대로.

## 5. 테스트 계획

픽스처: 실제 templates/회의록_양식.hwpx에 header.xml 무수정으로 안전한 기존 ID만 재사용해
표1을 형제 hp:p로 추가한 변형본(`_multi_table_fixture`) — test_minutes.py의 _degraded_template
패턴 계승. **스캔/생성 테스트가 같은 픽스처 공유**(표 번호 계약 증명의 전제).

핵심 케이스:
- 표 인덱싱 계약: scan의 표1 = build가 쓴 표1(물리 엘리먼트 대조), 사진표 보존과 표1 쓰기 동시.
- 하위호환: 2요소 cell_map 기존 테스트 무수정 그린, 2/3요소 혼합, v1/v2/v3 라운드트립,
  is_standard_map 3요소 회귀.
- 후검증: 실존성(신규), 인접성(병합 폭·표 스코프), 공백 비대칭, slots↔pins 교차 중복,
  한 행 2쌍 교차 배정 거부, 전폭 제목행 배제. `_neighbor_left/above`는 순수 함수 단위 테스트.
- 불변식: participants=[] × 표1 매핑 → 문단 ≥1 (한글 크래시 방지).
- 경계: table 범위 밖 → 경고+건너뛰기, 크래시 없음.
- UI: test_ui_parser의 mnDeriveCustomSlots 2건 갱신(표0 제한 → 다중표 + ID 규칙).
- 수동 관찰(기존 관례): 표1 핀 저장→재오픈 복원, 표1 매핑 생성물을 실제 한글에서 열어 확인.

## 6. 구현 순서 (계획 수립 시 세분화)

1. 엔진: _iter_top_tables 이동 + fieldmap v3 + build_minutes 다중 표 + 테스트 (UI 없이 완결)
2. AI: map_minutes_form + 직렬화 + 후검증 + 테스트 (모킹)
3. 백엔드: 통합 엔드포인트 + 레거시 제거 + test_minutes_api 이관
4. UI: 버튼 통합 + 잔재 정리 + normCellMap 수정 + test_ui_parser 갱신
5. 실기 검증: 실제 다중 표 양식으로 매핑→저장→생성→한글 열기

## 7. 위험과 완화 (요약)

| 위험 | 완화 |
|---|---|
| 스캔↔생성 표 번호 불일치 | 같은 함수 공유(물리적 단일 소스) |
| is_standard_map 비교 누락 | 전용 회귀 테스트 |
| normCellMap 절단으로 표 정보 유실 | 수정 + UI 관찰 검증 |
| UI 1-based(표 N) vs 내부 0-based | 주석·네이밍 명시 |
| AI 억지 채움 | unmapped 코드 계산 + 후검증 + schema=None |
| 매핑 후 템플릿 교체로 표 수 변동 | _resolve 경고+건너뛰기(크래시 없음) |
