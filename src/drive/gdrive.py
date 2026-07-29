# -*- coding: utf-8 -*-
"""Google Drive 연동 — 데스크톱 OAuth(InstalledAppFlow) + 업로드.

준비물(사용자):
  Google Cloud Console에서 'OAuth 클라이언트 ID(데스크톱 앱)'를 만들고
  client_secret.json을 프로그램 폴더에 저장.

토큰은 token.json에 캐시(현 계정에서 재인증 불필요). 라이브러리 미설치 시
모든 함수가 안전하게 실패 상태를 반환한다(앱 본기능에 무영향).
"""
import os

from src.paths import data_root, data_path

_PKG_BASE = data_root()


def _base_dir():
    """client_secret.json 위치: 프로그램 폴더(EXE 옆) 우선, 없으면 현재 작업 폴더."""
    for d in (_PKG_BASE, os.getcwd()):
        if d and os.path.exists(os.path.join(d, "client_secret.json")):
            return d
    return _PKG_BASE


BASE = _PKG_BASE
TOKEN_PATH = data_path("token.json")


def _client_secret_path():
    return os.path.join(_base_dir(), "client_secret.json")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]  # 이 앱이 만든 파일만

_MIME = {
    ".hwp": "application/x-hwp",
    ".hwpx": "application/hwp+zip",
    ".pdf": "application/pdf",
    ".json": "application/json",
}


def libs_available() -> bool:
    try:
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
        return True
    except Exception:
        return False


def has_client_secret() -> bool:
    return os.path.exists(_client_secret_path())


def _load_creds():
    """저장된 토큰 로드 + 만료 시 자동 갱신. 없으면 None."""
    if not os.path.exists(TOKEN_PATH):
        return None
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_creds(creds)
        except Exception:
            return None
    return creds if (creds and creds.valid) else None


def _save_creds(creds):
    with open(TOKEN_PATH, "w", encoding="utf-8") as fp:
        fp.write(creds.to_json())


def status() -> dict:
    """연결 상태 요약 (UI 표시용)."""
    info = {"lib": libs_available(), "client_secret": has_client_secret(),
            "connected": False, "email": ""}
    if not info["lib"]:
        return info
    try:
        creds = _load_creds()
        if creds:
            info["connected"] = True
            # 이메일은 토큰에 없을 수 있으므로 생략 가능
    except Exception:
        pass
    return info


def connect() -> dict:
    """브라우저 OAuth 동의 흐름 실행 → 토큰 저장."""
    if not libs_available():
        return {"ok": False, "error": "google-api-python-client/google-auth-oauthlib가 설치되지 않았습니다. pip install로 설치하세요."}
    if not has_client_secret():
        return {"ok": False, "error": "client_secret.json이 프로그램 폴더에 없습니다. Google Cloud Console에서 데스크톱 OAuth 클라이언트를 만들어 받으세요."}
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(_client_secret_path(), SCOPES)
        # 로컬 임시 서버로 리디렉션 받기. timeout_seconds로 무한 대기(UI 멈춤) 방지.
        creds = flow.run_local_server(port=0, prompt="consent",
                                      authorization_prompt_message="",
                                      timeout_seconds=180)
        _save_creds(creds)
        return {"ok": True}
    except Exception as e:
        msg = str(e)
        if "timed out" in msg.lower() or "timeout" in msg.lower():
            msg = "인증 시간이 초과됐습니다(3분). 브라우저에서 로그인·허용을 완료한 뒤 다시 시도하세요."
        return {"ok": False, "error": f"Drive 인증 실패: {msg}"}


def disconnect() -> dict:
    try:
        if os.path.exists(TOKEN_PATH):
            os.remove(TOKEN_PATH)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _service():
    from googleapiclient.discovery import build
    creds = _load_creds()
    if not creds:
        return None
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _ensure_folder(svc, name):
    """이름의 폴더를 찾고 없으면 생성 → folderId."""
    if not name:
        return None
    q = ("mimeType='application/vnd.google-apps.folder' and trashed=false "
         f"and name='{name.replace(chr(39), chr(92) + chr(39))}'")
    res = svc.files().list(q=q, spaces="drive", fields="files(id,name)",
                           pageSize=1).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    folder = svc.files().create(body=meta, fields="id").execute()
    return folder.get("id")


def upload_files(paths, folder_name="") -> dict:
    """여러 파일을 Drive(지정 폴더)에 업로드. 반환: {ok, links:[{name,link}]}"""
    if not libs_available():
        return {"ok": False, "error": "Drive 라이브러리 미설치"}
    try:
        from googleapiclient.http import MediaFileUpload
    except Exception as e:
        return {"ok": False, "error": f"Drive 라이브러리 오류: {e}"}
    try:
        svc = _service()
    except Exception as e:
        return {"ok": False, "error": f"Drive 연결 오류: {e}"}
    if not svc:
        return {"ok": False, "error": "Drive 미연결 (먼저 연결하세요)"}
    try:
        folder_id = _ensure_folder(svc, folder_name)
        links = []
        for p in paths:
            if not p or not os.path.exists(p):
                continue
            ext = os.path.splitext(p)[1].lower()
            meta = {"name": os.path.basename(p)}
            if folder_id:
                meta["parents"] = [folder_id]
            media = MediaFileUpload(p, mimetype=_MIME.get(ext, "application/octet-stream"),
                                    resumable=False)
            f = svc.files().create(body=meta, media_body=media,
                                   fields="id,webViewLink,name").execute()
            links.append({"name": f.get("name"), "link": f.get("webViewLink", "")})
        if not links:
            return {"ok": False, "error": "업로드할 파일이 없습니다."}
        return {"ok": True, "links": links}
    except Exception as e:
        return {"ok": False, "error": f"Drive 업로드 실패: {e}"}
