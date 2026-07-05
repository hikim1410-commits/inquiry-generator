# 릴리스 절차

## 사전 준비 (최초 1회)

1. **GitHub 저장소 개설** — 공개(Public) 권장 (비공개는 API 토큰 필요).
2. **`src/version.py` 의 `GITHUB_REPO` 확정**:
   ```python
   GITHUB_REPO = "owner/repo"  # 예: naevion/inquiry-generator
   ```
3. GitHub CLI 설치: `winget install GitHub.cli` 후 `gh auth login`.

---

## 신규 릴리스 단계

### 1. 버전 올리기

`src/version.py` 의 `__version__` 수정:
```python
__version__ = "1.2.1"   # 이전: "1.2.0"
```
- **patch**: 버그 수정, 소규모 개선
- **minor**: 기능 추가 (하위 호환)
- **major**: 대규모 변경, 하위 비호환

### 2. 빌드

```bat
rem 표준 빌드 환경 = .venv-build (Python 3.13) — spec의 버전 가드가 다른 버전을 차단
.venv-build\Scripts\python.exe -m PyInstaller navion_quote.spec --noconfirm
```
빌드 후 `dist\내비온 견적서 생성기\내비온 견적서 생성기.exe` 확인.
파일 우클릭 → 속성 → 자세히 탭에 버전 표기(예: `1.2.1.0`) 확인.

### 3. 배포 ZIP 생성

```bat
rem 권장: kordoc-runtime/node_modules 제외 (~70 MB)
py tools/make_release_zip.py

rem kordoc 의존성이 바뀌었을 때만 전체 포함 (~1 GB)
py tools/make_release_zip.py --full
```
생성 결과: `dist\내비온 견적서-회의록 생성기 v1.2.1.zip`

> **코드만 담는 부분 ZIP이 권장인 이유:**  
> 설치 시 사용자 PC의 기존 `kordoc-runtime` 폴더는 robocopy `/E` (덮어쓰기, 삭제 없음)로
> 그대로 보존된다. kordoc 의존성(node_modules)이 바뀌지 않은 일반 릴리스에서는 불필요.

### 4. GitHub Release 생성

```bat
gh release create v1.2.1 ^
  "dist\내비온 견적서-회의록 생성기 v1.2.1.zip" ^
  --title "v1.2.1" ^
  --notes "## 변경 내용\n- 버그 수정: ...\n- 개선: ..."
```

- 태그는 반드시 `v{__version__}` 형식 (예: `v1.2.1`). 앱이 `tag_name`을 읽어 비교.
- `--notes` 내용이 앱의 "변경내역"란에 표시된다.
- **draft / prerelease 는 앱이 자동 감지하지 않음** (GitHub API `releases/latest`는 draft·prerelease 제외).

---

## 자동 업데이트 흐름 (앱 측)

앱 시작 시 → `check_update()` 백그라운드 실행 → 새 버전이면 배너 표시.  
배너 또는 설정 탭의 "지금 업데이트" → ZIP 다운로드 → 압축 해제 →  
`%TEMP%\navion_update\apply_update.ps1` (PowerShell, detached) → 앱 종료 →
install 폴더 내 잔여 node.exe 종료 → robocopy로 파일 교체(실패 시 1회 재시도) →
교체 성공 시에만 새 버전 재실행. 교체 실패면 손상 방지를 위해 재실행을 중단하고 안내한다.

---

## 롤백

문제가 생기면 이전 ZIP을 `dist\내비온 견적서 생성기\` 에 수동으로 풀어 덮어쓰거나,
GitHub Release의 이전 버전 ZIP을 배포 링크로 공유한다.
