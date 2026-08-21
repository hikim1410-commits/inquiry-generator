# 교훈 기록 (Lessons Learned)

## 2026-07-22: 경비 '경비' 라벨 미병합 — 병합 실패가 아니라 진단 코드 크래시로 인한 오진
- **발생**: 템플릿의 경비 카테고리 열이 A17(2행 병합)+개별 6셀로 깨져 있어 산출물에서
  '경비' 라벨이 위 2행만 덮임(육안 결함). 6/17 밤 복구 스크립트(`_fix_template.py`)가
  실패한 것으로 보였고, "TableMergeCell 병합 실패"로 오진해 hwp_writer에
  BreakLine 우회(미커밋)를 만들다 중단된 상태였다.
- **원인(확정, 2026-07-22 재현)**: 병합 시퀀스
  `TableCellBlock→TableCellBlockExtend→TableLowerCell×N→TableMergeCell`은
  **이미 병합된 셀에서 시작해도 정상 동작**한다. 복구 스크립트는 병합·검증까지
  성공한 뒤, **진단용 `get_selected_text()`가 `InitScan(Range=0xFF)` COM 오류로
  크래시**(v1.5.1의 dynamic.Dispatch 지연 바인딩은 named 인자 미지원)해
  `save_as` 직전에 죽었다 → 저장이 안 됐을 뿐인데 "병합 실패"로 보였다.
- **수정**: 진단 읽기를 try/except로 무해화 후 재실행 → 병합 저장 성공(248,320B).
  필드 117개 집합 동일·재열기 후 병합 유지 확인, 템플릿 교체. hwp_writer의
  BreakLine 우회(오진 기반, 첫 행 셀만 늘려 병합과 비등가)는 폐기하고 커밋본
  (검증된 TableMergeCell 시퀀스) 복원. `pytest -m hwp` 6건 green +
  exp12 PDF 렌더 육안 확인('경비'가 12행 전체 관통).
- **재발 방지**: `test_template_expense_category_merged` 추가 — 템플릿 경비
  카테고리 열이 단일 병합 셀인지 KeyIndicator 주소로 검증(-m hwp).
- **교훈**: ① dynamic.Dispatch 환경에선 pyhwpx 메서드 중 named 인자를 쓰는 것
  (`get_selected_text`→InitScan 등)이 COM 오류를 낸다 — 진단·부가 경로에서도
  본 작업을 죽이지 않게 격리할 것. ② "병합 실패"처럼 보이는 증상은 어느 단계에서
  죽었는지(병합? 검증? 저장?) 로그로 특정한 뒤 진단할 것 — 성공한 작업이
  저장 전 크래시로 증발하면 실패로 오진된다.

## 2026-07-02: 업데이트 적용 실패 — 동결 빌드에서 "unknown encoding: utf-8-sig" (v1.4.0)
- **발생**: 실제 사용자 PC에서 [지금 업데이트] 클릭 → "업데이트 스크립트 작성 실패:
  unknown encoding: utf-8-sig" 토스트. `%TEMP%\navion_update\apply_update.ps1`이
  0바이트로 남음(파일은 열렸지만 `fp.write` 전에 예외 발생 — 트렁케이트만 되고 씀은 실패).
- **원인(강한 정황 증거, 재현은 못함)**: `open(path, "w", encoding="utf-8-sig")`는
  순수파이썬 `encodings.utf_8_sig` 모듈을 **첫 사용 시점에 동적 import**한다. 반면
  `apply_update()`는 pywebview JS↔Python 브릿지 콜백(메인 스레드가 아님)에서 호출된다.
  PyInstaller 동결 빌드 + 비-메인 스레드에서 특정 코덱을 그 프로세스 최초로 사용할 때
  간헐적으로 `LookupError: unknown encoding: ...`가 나는 사례가 CPython/PyInstaller
  생태계에 보고돼 있다. `utf-8`/`ascii`는 인터프리터에 내장(C)이라 이 문제가 없지만
  `utf-8-sig`는 동적 import가 필요해 취약하다.
- **수정**: `updater.py`의 BOM 작성을 `encoding="utf-8-sig"` 대신 바이너리 모드로
  `b"\xef\xbb\xbf"` + `text.replace("\n","\r\n").encode("utf-8")`로 직접 구성(바이트
  단위로 기존 결과와 동일함을 로컬 검증). `kordoc.py::_read_text_passthrough`의
  `raw.decode("utf-8-sig")`도 같은 취약점이라 BOM 수동 스트립 + `utf-8`/`cp949`
  디코드로 교체.
- **방지책**: 앱 어디서든 `"utf-8-sig"`(또는 첫 사용이 드문 인코딩)를 새로 쓰지 말 것.
  BOM이 필요하면 `b"\xef\xbb\xbf"` + 내장 `"utf-8"` 조합으로 직접 만든다.
- **남은 위험**: 사용자 PC의 설치본이 이 실패 도중 robocopy 재시도(exit 11=부분 실패)까지
  겹쳐 `_internal`이 신/구 파일 혼재 상태일 수 있음 — 재현 확인 시 클린 재설치 권장.

## 2026-06-17: 견적서 '읽기 전용'의 진범 — 생성 후 문서를 안 닫아 백그라운드 한글이 파일 잠금 (v1.2.7)
- **발생**: 생성된 견적서를 열면 한글이 '읽기 전용'으로 연다. OS 읽기전용 비트 해제
  (`_clear_readonly`, chmod)를 v1.2.6에 넣었는데도 "여전히 동일".
- **원인(확정)**: `HwpWorker`는 한글 COM 세션을 **재사용**(종료 안 함)하는데,
  `_fill_document`이 `save_as` 후 문서를 **닫지 않았다**. 숨김 한글이 산출물 파일을
  계속 열어둔 채 잡고 있어, 사용자가 그 파일을 열면 한글이 "다른 곳에서 사용 중"
  → 읽기 전용으로 연다. 파일 OS 속성과 무관 → chmod로는 절대 안 풀린다.
  (대조: `_scan_fields_com`은 끝에 `FileClose`를 호출해 잠금을 풀고 있었음 — 차이가 단서.)
- **수정**: `_fill_document` 끝(저장·PDF 후)에 `hwp.Run("FileClose")` 추가.
- **검증**: 세션 유지 상태에서 산출물 `os.replace`(rename) 왕복 성공 = 잠금 풀림.
  회귀 테스트 `test_output_not_locked_under_persistent_session` 추가(OS속성만 보던 기존
  테스트는 이 원인을 못 잡음).
- **방지책**: COM 세션을 재사용하는 모든 경로는 파일 작업 끝에 반드시 `FileClose`로
  핸들을 풀 것. "쓰기 가능(W_OK)"만 검사하지 말고 "잠금 해제(rename 가능)"까지 검증.

## 2026-06-17: 경비 8행 초과 동적 추가 행이 '경비' 세로병합에서 빠짐 (v1.2.7)
- **발생**: 경비 9개 이상이면 추가된 행의 좌측에 '경비' 라벨과 분리된 빈 셀이 생김(PDF 확인).
- **원인**: `_expand_expense_rows`가 행만 추가하고 좌측 카테고리 열(‘경비’)을 병합 안 함.
  새 행은 세로병합 영역 밖에 새 셀로 생성됨.
- **수정**: 행 추가 후 exp_name 좌측('경비' 라벨셀)에서
  `TableCellBlock → TableCellBlockExtend → TableLowerCell×추가행수 → TableMergeCell`.
  실측으로 확정한 시퀀스(처음엔 Extend 없이 시도해 MergeCell이 False 반환 → Extend 필수).
- **검증**: 경비 12개 케이스를 PDF로 렌더해 '경 비'가 12행 전체를 덮는 것 육안 확인.

## 2026-06-17: kordoc 비내장화 — node.exe+npm만 동봉, kordoc은 첫 사용 시 npm 설치 (v1.2.7)
- **배경**: 빌드 spec이 `kordoc-runtime/`(node_modules 754MB)를 통째로 dist에 복사 →
  배포본 비대. `ensure_kordoc`는 이미 npm 설치 구조였는데 미리 깔아 통째 넣어 이점이 죽음.
- **수정**: spec은 `kordoc-runtime/_nodejs/`에 node.exe + npm(node_modules/npm + npm.cmd)만
  동봉. 첫 변환 때 `ensure_kordoc`이 `kordoc@3`(검증 메이저 내 최신) + `pdfjs-dist@4`를 설치.
  운영 호출은 `.cmd` 셸 의존 없이 `[node.exe, npm-cli.js] install ...`로 직접 실행.
- **검증**: 임시 `_nodejs/`로 시스템 Node 없이 `npm --version`(11.11.0)·소형 패키지 install 성공.
- **유의**: 업데이터는 `robocopy /E`(삭제 없음)라 사용자가 받아둔 `node_modules/kordoc`는
  업데이트 시 보존됨(재다운로드 불필요). 빌드머신엔 Node.js 설치 필수(없으면 spec 경고).

## 2026-06-10: bat 더블클릭 시 무반응/창 깜빡임 — UTF-8 한글 bat이 CP949 cmd에서 파싱 즉사 (진범)
- **발생**: `견적서생성기.bat` 더블클릭 → 콘솔이 떴다 바로 꺼지고 앱은 시작조차 안 됨.
- **원인(확정)**: bat 파일을 **UTF-8(한글 포함)**로 저장했는데, 한국어 Windows cmd는
  배치 파일을 **CP949로 해석**한다. UTF-8 한글 바이트가 CP949 2바이트 문자로 오독되며
  **줄바꿈(\r\n)까지 집어삼켜** 명령줄이 엉뚱한 위치에서 잘림 — 실측 증거:
  `'t.txt로' is not recognized as an internal or external command`.
  줄머리 `chcp 65001`로도 **못 막는다**(이후 줄도 깨짐). 배치가 즉시 abort → "창 깜빡 후 무반응".
- **방지책(철칙)**: **.bat/.cmd 파일 내용은 100% ASCII로만 작성**(한글 메시지·주석 금지,
  한글은 파일명까지만 허용). 한글 안내가 필요하면 Python 쪽에서 출력.
- **검증**: ASCII 재작성 후 Start-Process(더블클릭과 동일 경로)로 실행 →
  콘솔 유지 + WebView2 자식 프로세스 +6 + `app-log.txt`에 "GUI 루프 진입" 기록.
- **부진범(같이 고침)**: bare `python`은 탐색기 컨텍스트에서 0바이트 Store 스텁
  (`WindowsApps\python.exe`)에 걸릴 수 있고, `py` 기본값(3.14)엔 deps가 없음 →
  런처는 `py -3.12` 1순위, `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` 폴백.
- **상시 진단 장치**: `app.py`가 시작~종료 전 과정을 `app-log.txt`에 기록(치명 오류 traceback 포함).
  `실행_디버그.bat`은 python/버전/deps 출력 후 앱 stdout을 `run-log.txt`로 수집.
- **기타**: 종료 시 `window_impl.cc ... Failed to unregister class` 경고는 무해.
  이 PC의 msedgewebview2 프로세스 ~18-30개는 타 앱 베이스라인 — 개수로 앱 기동 판단 금지(델타로 판단).

## 2026-06-11: 클라이언트 PC 즉시 종료 — MOTW(다운로드 차단)가 .NET DLL 로드를 거부
- **발생**: 카카오톡으로 받은 ZIP을 풀어 실행한 클라이언트 PC에서 앱 즉사.
  팝업: `Failed to resolve Python.Runtime.Loader.Initialize from ..._internal\pythonnet\runtime\Python.Runtime.dll`.
  같은 바이너리가 빌드 PC·로컬 dist에선 정상 — **실행 위치(Downloads)만 다름**.
- **원인(확정)**: 브라우저/카카오톡 수신 ZIP → 탐색기 압축 해제 시 모든 파일에
  `Zone.Identifier`(인터넷 존=3) NTFS 스트림 부착. .NET Framework는 인터넷 존
  어셈블리를 기본 거부(0x80131515) → pythonnet의 `Assembly.LoadFrom` 실패 →
  pywebview가 `import clr` 하는 순간 사망. PyInstaller 부트로더와 python3xx.dll은
  Win32 LoadLibrary 경유라 MOTW 무시 → "Api 초기화 완료"까지는 정상 진행되는 함정.
- **방지책(이중 방어, 2026-06-11 적용)**:
  ① `내비온 견적서 생성기.exe.config`(loadFromRemoteSources=true)를 EXE 옆에 동봉
     — 단 PyInstaller 6.x는 datas를 전부 `_internal/`로 보내므로 **datas로는 불가**,
     spec의 COLLECT 뒤에서 `shutil.copy`로 최상위에 배치해야 함.
  ② `app.py` frozen 분기에서 `import webview` 이전에 `_internal` 하위 `*.dll`의
     `:Zone.Identifier` ADS를 `DeleteFileW`로 제거(try/except, 부팅 차단 금지).
- **즉시 조치(재빌드 전 클라이언트)**: PowerShell
  `Get-ChildItem -Path "<설치 폴더>" -Recurse | Unblock-File`
  또는 ZIP 우클릭→속성→[차단 해제] 후 압축 해제.
- **검증**: 재빌드 후 dist 파일들에 Zone.Identifier를 인위 부착(`Set-Content -Stream`)
  → Unblock 없이 실행되는지 시뮬레이션 (M15 패키징 스모크에 포함).
- **부수 교훈**: 배포 V1.1은 AIDEN-DESKTOP의 Python 3.13으로 빌드돼 이 PC(3.12/3.14)와
  드리프트. 빌드는 **py -3.12로 통일**(spec·bat 표기와 일치), 빌드 PC 변경 시 버전 먼저 확인.

## 2026-07-07: 견적서 COM 사망 + 회의록 한글 크래시 — v1.5.1 이중 수정
- **발생①(견적서)**: v1.5.0(3.13 빌드) 업데이트 직후 견적서 생성 시
  `TypeError: Only strings and iids can be converted to a CLSID.`
  (pyhwpx `EnsureDispatch("HWPFrame.HwpObject")` — 올바른 문자열 인자인데도 실패).
  자가복구(서버 선기동·gen_py 정리) 전부 무효. 실패 시도마다 Hwp.exe -Automation
  좀비 +1 → 32개 누적 실측.
- **원인①(확정)**: PyInstaller 동결 빌드에 `pywintypes313.dll`이 두 경로
  (`_internal\` 루트 + `_internal\pywin32_system32\`)로 들어가 **이중 초기화** →
  makepy 클래스의 CLSID(PyIID)와 pythoncom의 PyIID가 서로 다른 인스턴스 타입 →
  `DispatchBaseClass.__init__`의 `QueryInterface(self.CLSID)`가 타입 인식 실패.
  같은 pywin32 312를 비동결 venv에서 돌리면 정상(=pywin32 버그 아님),
  같은 DLL을 이름 바꿔 2회 로드하면 동일 오류 재현(기계론 증명).
- **수정①**: `hwp_writer._use_dynamic_dispatch()` — pyhwpx가 쓰는
  `gencache.EnsureDispatch`를 `win32com.client.dynamic.Dispatch`(지연바인딩)로
  교체. makepy/CLSID를 아예 안 쓰므로 원천 회피. pyhwpx는 `constants` 미사용이라
  안전(실기에서 생성→insert_text→quit 검증). 초기 설계(2026-06 dynamic.Dispatch
  late binding)로의 복귀이기도 함.
- **발생②(회의록)**: 생성은 성공하는데 자동 열기에서 한글이 즉사 →
  사용자에겐 "생성 실패"로 보임. 이벤트 로그: HwpApp.dll+0x254854, 0xc0000005,
  매번 동일(6/25·6/30에도 동일 시그니처 — v1.2.8 시절부터 잠복).
- **원인②(확정, GUI 이분탐색 3회)**: `participants`가 빈 배열이면
  `build_minutes`가 참석자 셀 문단을 전부 제거 후 아무것도 안 넣어
  **문단 0개 subList 셀** 생성 → 한글은 "셀당 최소 1문단" 불변식 위반 파일을
  여는 즉시 하드 크래시. zip/XML 문법 검증으론 못 잡는 의미론 결함.
- **수정②**: 참석자 비면 빈 문단 1개 강제 삽입(hwpx_minutes.py) +
  test_empty_participants에 "모든 셀 문단 ≥1" 불변식 검증 추가.
- **부진범(같이 고침)**: 업데이터 robocopy가 /E(비퍼지)라 3.12→3.13 점프에서
  구빌드 잔재 5,726개(88.7MB, python312.dll·cp312 .pyd·중복 dist-info) 잔존.
  → 2단 robocopy로 변경: 루트 /E(사용자 데이터 보존) + `_internal` /MIR(퍼지).
- **교훈**: ① HWPX '유효성'은 zip/XML 문법이 아니라 **한글이 실제로 열리는가**로
  검증할 것(셀당 최소 1문단 같은 의미론 불변식 존재). ② COM 실패 경로에서
  기동된 서버 프로세스는 좀비로 남는다 — 실패 시각과 tasklist 대조가 진단 지름길.
  ③ onedir 업데이트에서 Python 마이너 점프는 비퍼지 복사와 상극.

## 2026-07-29: 첨부 PDF 파싱 실패 (스캔 PDF) + 감사에서 나온 읽기/쓰기 결함 일괄
- **발생**: 견적서 AI 초안에서 Downloads의 PDF 첨부가 "텍스트를 추출하지 못했습니다"로
  실패. 실측 재현 결과 해당 PDF는 텍스트 레이어가 없는 스캔(이미지 2장, 텍스트 0자).
- **원인**: kordoc(pdfjs)·pypdf·fitz 모두 텍스트 레이어가 없으면 0자 — OCR 부재가
  구조적 원인. (기각한 가설: 배포 zip npm 누락 — v1.6.0 zip은 정상, 단 로컬 dist
  폴더는 _nodejs/node_modules 누락 상태였음 / UI 배선 문제 — 텍스트 PDF는 2,847자
  정상 변환 / kordoc 3.18.1 회귀 — 없음)
- **수정**: empty_output PDF에 한해 pypdfium2 렌더 → 설정된 LLM 비전 전사 폴백
  (src/convert/pdf_ocr.py + llm.complete_text + SystemApi._vision_pdf_fallback).
  실측: 스캔 견적서 1페이지 → 1,626자 마크다운(표 포함) 전사 성공.
- **부진범(빌드)**: navion_quote.spec이 npm 패키지 부재 시 조용히 건너뜀 →
  npm 없는 번들 출고 가능성. SystemExit로 빌드 중단하게 변경.
- **교훈**: ① "PDF 파싱 실패"는 파일마다 원인이 다르다 — 텍스트 레이어 유무를
  fitz/pypdf로 먼저 가려라. ② 빌드 스펙의 조건부 copy는 누락 시 fail-loud가 원칙.

## 2026-07-29: HWP 읽기/쓰기 감사 결함 13건 (Opus 감사 2팀 교차확인)
- **HWP 바이너리 파서**: inline 컨트롤(4~9,19,20)을 2바이트만 소비해 본문 오염
  ('수신기관명汫ॣ') — 확장 컨트롤과 동일하게 16바이트 소비로 수정(hwp_scan.py).
- **AI 재매핑 데이터 소실**: save_minutes_fieldmap이 custom_slots/annotations 없이
  파일을 통째로 재작성 → [AI 자동 매핑] 클릭만으로 사용자 핀 영구 소실. 기존 값
  승계로 수정. 같은 파일을 두 스키마로 쓰는 이중 저장 경로가 근본 원인.
- **커스텀 견적서 템플릿 no-op**: scan_template이 스캔만 하고 생성은 항상 내장
  템플릿 사용(리팩토링 이전부터). data 폴더 복사 + 워커 재생성으로 실제 적용되게 수정.
- 그 외: 스캔 좌표 하드코딩(사이드카 우선), subList None 크래시, .work.hwp 잔류,
  config 손상 시 무백업 덮어쓰기, updater _internal 부재 오판 등 — 상세는 커밋 참조.
- **교훈**: ① 읽기 경로와 쓰기 경로가 좌표/규칙을 공유하지 않으면 반드시 어긋난다
  (쓰기는 cell_map, 읽기는 고정 좌표). ② 같은 파일을 쓰는 저장 함수가 2개면
  한쪽이 다른 쪽 키를 지운다 — 병합 저장이 기본값이어야 한다.

## 2026-08-21: v2.0.0 배포본 견적서/회의록 생성 전면 불능 (dateutil.easter 누락)
- **현상**: 생성 버튼마다 "partially initialized module 'pandas' ... has no attribute
  '_pandas_datetime_CAPI' (most likely due to a circular import)". 재시도해도 동일.
- **진짜 첫 실패**: 그보다 앞선 로그에 `No module named 'dateutil.easter'`.
  pandas 에러는 2차 증상이었다 — 첫 import 실패로 pandas가 sys.modules에 부분
  초기화 상태로 남고, 이후 모든 재시도가 CAPI 부재로 영구 실패(앱 재시작 전까지 회복 불가).
- **원인**: `pandas._libs.tslibs.offsets`(컴파일된 .pyd)가 런타임에만 dateutil.easter를
  import → PyInstaller 정적 분석으로는 원리적으로 감지 불가. pandas 소스에서 이를
  정적 import 하는 곳은 `tests/tseries/offsets/test_easter.py` 뿐이라, 빌드 머신의
  tests 수집 여부에 따라 우연히 들어갔다 빠졌다 한다. v1.9.0(pandas 3.0.5) 빌드는
  우연히 통과, v2.0.0(pandas 3.0.3, 다른 PC) 빌드에서 누락.
- **격리**: `import pandas`만 하는 최소 exe로는 재현 안 됨 → `--exclude-module
  dateutil.easter`로 빌드하니 사용자 로그와 동일한 traceback 재현. 배포본 exe의
  PYZ를 ZlibArchiveReader로 열어 dateutil.easter/rrule 부재를 직접 확인.
- **수정**: navion_quote.spec hiddenimports에 "dateutil.easter", "dateutil.rrule" 명시.
- **현장 핫픽스(재빌드 없이)**: 배포본 `_internal/dateutil/`에 easter.py·rrule.py를
  복사하면 즉시 복구된다. FrozenImporter가 잡은 패키지 __path__가 `_internal/<pkg>`라
  폴더가 원래 없어도 새로 만들어 넣으면 로드된다(동일 조건 테스트 exe로 검증).
- **교훈**: ① 반복되는 에러 메시지는 2차 증상일 수 있다 — 로그를 처음까지 거슬러
  올라가 *첫* 실패를 찾아라. ② 컴파일된 확장 모듈(.pyd) 안의 import는 PyInstaller가
  못 본다. 우연히 딸려온 의존성은 언제든 빠진다 → hiddenimports에 명시가 유일한 해법.
  ③ import 실패로 부분 초기화된 모듈은 프로세스가 살아있는 한 계속 독이 된다.
