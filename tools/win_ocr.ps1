# Windows 내장 OCR(Windows.Media.Ocr) 실행기 — 이미지들의 단어 좌표를 JSON으로 낸다.
#
# 호출:
#   powershell -NoProfile -ExecutionPolicy Bypass -File win_ocr.ps1 -PathsFile <목록파일>
#   powershell -NoProfile -ExecutionPolicy Bypass -File win_ocr.ps1 -CheckOnly
#
# 목록파일: UTF-8, 한 줄에 이미지 절대경로 하나.
#   (-Paths 배열로 직접 받지 않는 이유 — -File 호출에서 PowerShell은 콤마열도 공백열도
#    원소 1개로만 바인딩한다. 실측 확인. 그대로 두면 다건이 조용히 1건만 처리된다.)
#
# 출력: {"ok":true,"lang":"ko","results":[{"path":..,"ok":..,"words":[{text,x,y,w,h}]}]}
[CmdletBinding()]
param(
    [string]$PathsFile,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8

function Fail($msg) {
    @{ ok = $false; error = $msg } | ConvertTo-Json -Compress
    exit 1
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
} catch {
    Fail "WinRT 초기화 실패: $($_.Exception.Message)"
}

function Await($op, $type) {
    $netTask = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

$engine = $null
try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko')) } catch {}
# 사용자 프로필 언어로 넘어가면 영어 엔진이 한글 영수증을 읽고 ok=true를 낸다.
# ko가 아니면 CheckOnly·본 실행 모두 실패. 조치: Windows 한국어 언어팩 설치.
$lang = ''
if ($null -ne $engine) { $lang = [string]$engine.RecognizerLanguage.LanguageTag }
if ($null -eq $engine -or $lang -notmatch '^ko(-|$)') {
    Fail "한국어 OCR 언어팩 미설치(lang=$lang) — Windows 설정에서 한국어 언어팩을 설치해야 합니다."
}

if ($CheckOnly) {
    @{ ok = $true; lang = $engine.RecognizerLanguage.LanguageTag; results = @() } | ConvertTo-Json -Depth 3 -Compress
    exit 0
}

if (-not $PathsFile) { Fail "-PathsFile 또는 -CheckOnly 가 필요합니다." }
if (-not (Test-Path -LiteralPath $PathsFile)) { Fail "목록 파일 없음: $PathsFile" }

# [string] 캐스팅 필수 — Get-Content 결과에는 PSPath 등 부가 속성이 붙어 있어
# 그대로 ConvertTo-Json 하면 경로가 문자열이 아니라 객체로 직렬화된다.
$targets = @(Get-Content -LiteralPath $PathsFile -Encoding UTF8 |
    ForEach-Object { [string]$_ } | Where-Object { $_.Trim() })

$results = foreach ($p in $targets) {
    try {
        if (-not [System.IO.Path]::IsPathRooted($p)) { throw "절대경로가 아님: $p" }
        if (-not (Test-Path -LiteralPath $p)) { throw "파일 없음: $p" }

        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($p)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $ocr = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

        $words = foreach ($line in $ocr.Lines) {
            foreach ($wd in $line.Words) {
                [pscustomobject]@{
                    text = $wd.Text
                    x    = [math]::Round($wd.BoundingRect.X, 1)
                    y    = [math]::Round($wd.BoundingRect.Y, 1)
                    w    = [math]::Round($wd.BoundingRect.Width, 1)
                    h    = [math]::Round($wd.BoundingRect.Height, 1)
                }
            }
        }
        $stream.Dispose()
        [pscustomobject]@{ path = $p; ok = $true; words = @($words) }
    } catch {
        [pscustomobject]@{ path = $p; ok = $false; error = $_.Exception.Message; words = @() }
    }
}

@{ ok = $true; lang = $engine.RecognizerLanguage.LanguageTag; results = @($results) } |
    ConvertTo-Json -Depth 5 -Compress
