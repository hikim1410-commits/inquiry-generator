# 교훈 기록 (Lessons Learned)

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
