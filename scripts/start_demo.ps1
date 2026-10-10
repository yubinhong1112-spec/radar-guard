<#
start_demo.ps1 — 시연 시작 (T-CC07c)

  실행: [내 PC PowerShell, 저장소 루트]
      powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -Jetson 192.168.35.217   # 젯슨 IP 필수
      powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1 -Jetson 127.0.0.1        # 재생기로 볼 때
  되돌리기: scripts\stop_demo.ps1

왜 있나
  Ollama 트레이 앱(ollama app.exe)은 내려받아 둔 설치 파일을 뜰 때 실행해 버전을
  바꾼다(10/09 20:44 에 실제로 그랬다). 시연은 0.40.2 에서 잰 답으로 준비했으므로
  트레이 앱 없이 설치된 ollama.exe serve 만 띄운다. 시작 프로그램의 Ollama.lnk 는
  %USERPROFILE%\Ollama_startup_backup\ 로 옮겨 두었다(되돌리려면 다시 옮긴다).

  단계마다 결과를 한 줄씩 낸다. 실패하면 그 자리에서 멈춘다.
#>
param(
    # 젯슨 IP — 핫스팟 DHCP 라 접속마다 바뀐다. 기본값을 두지 않는다.
    [string]$Jetson,
    [string]$OllamaVersion = '0.40.2'
)
if (-not $Jetson) {
    Write-Host '젯슨 IP 를 넣으십시오(예: -Jetson 192.168.35.217) · 확인: 핫스팟 연결 기기 목록 또는 젯슨에서 `hostname -I`'
    exit 1
}
$root = Split-Path $PSScriptRoot -Parent
$ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'

function Wait-Until([scriptblock]$Test, [int]$Seconds) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) {
        if (& $Test) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

# 1. DB — 재부팅 직후에는 Docker 엔진이 아직 뜨는 중일 수 있다.
if (-not (Wait-Until { docker info *> $null; $LASTEXITCODE -eq 0 } 180)) {
    Write-Host '[1/4] 실패 — Docker 엔진이 응답하지 않는다. Docker Desktop 을 켠다.'; exit 1
}
docker start radar-guard-db *> $null
if (-not (Wait-Until { docker exec radar-guard-db pg_isready *> $null; $LASTEXITCODE -eq 0 } 60)) {
    Write-Host '[1/4] 실패 — radar-guard-db 가 접속을 받지 않는다.'; exit 1
}
Write-Host '[1/4] DB radar-guard-db 접속 가능'

# 2. 트레이 앱과 그것이 띄운 서버를 내린다.
$old = @(Get-Process -Name 'ollama app', 'ollama' -ErrorAction SilentlyContinue)
$tray = @($old | Where-Object { $_.ProcessName -eq 'ollama app' }).Count
$old | Stop-Process -Force
Start-Sleep -Seconds 2
Write-Host "[2/4] 트레이 앱 $tray 개 · 기존 Ollama 프로세스 $($old.Count)개 종료"

# 3. 설치된 ollama.exe serve 만 띄우고 버전을 확인한다.
Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
if (-not (Wait-Until { try { Invoke-WebRequest 'http://127.0.0.1:11434/api/version' -UseBasicParsing -TimeoutSec 2 | Out-Null; $true } catch { $false } } 60)) {
    Write-Host '[3/4] 실패 — ollama serve 가 60초 안에 응답하지 않는다.'; exit 1
}
$ver = (& $ollama --version | Out-String).Trim()
if ($ver -notmatch [regex]::Escape($OllamaVersion) + '\s*$') {
    Write-Host "[3/4] 경고 — Ollama 버전이 $OllamaVersion 이 아니다: $ver"
    Write-Host '      시연 답은 이 버전에서 잰 것이다. 관제 화면을 띄우지 않고 멈춘다.'; exit 1
}
Write-Host "[3/4] ollama serve 실행 · $ver"

# 4. 관제 화면. 챗봇 워밍업은 화면이 뜬 뒤 스스로 돈다(AI 상태 카드에 표시).
#    앱이 찍는 줄('[AI] 챗봇 워밍업 N초 · 준비 완료' 등)은 로그 파일로 받는다.
$log = Join-Path $env:TEMP 'radar_guard_console.log'
$ui = Start-Process -FilePath 'python' -WorkingDirectory $root -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
    -ArgumentList '-u', "`"$(Join-Path $root '01_현행코드\console_ui.py')`"", '--live', $Jetson
Write-Host "[4/4] 관제 화면 실행 · 젯슨 $Jetson · PID $($ui.Id) · 로그 $log"
