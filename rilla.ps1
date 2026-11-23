# One-command control for Rilla on Windows.   Usage:  .\rilla <command>      (run  .\rilla help  to list them)
param(
  [Parameter(Position = 0)][string]$Command = "help",
  [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest
)
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$Name = if ($env:RILLA_USER) { $env:RILLA_USER } else { "Suresh" }   # whose account to open; set RILLA_USER to change
$Port = 8000

function Say($m) { Write-Host $m -ForegroundColor Cyan }
function Fail($m) { Write-Host $m -ForegroundColor Red; exit 1 }

function Need-Docker {
  docker info *> $null
  if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop, wait until it says running, then try again." }
}

function Wait-Ready {
  Say "Waiting for Rilla to start (the first time can take a minute)..."
  for ($i = 0; $i -lt 60; $i++) {
    try {
      $r = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$Port/api/health" -TimeoutSec 3
      if ($r.StatusCode -eq 200) { Say "Rilla is up."; return }
    } catch { }
    Start-Sleep -Seconds 2
  }
  Write-Host "Rilla did not answer in 2 minutes. Look at the log:  .\rilla logs" -ForegroundColor Yellow
  exit 1
}

function Open-App {
  $out = docker compose exec -T api python -m app.cli link $Name
  $line = $out | Where-Object { $_ -match "\?t=" } | Select-Object -Last 1
  if (-not $line) { Fail "Could not get a sign-in link. Check:  .\rilla status" }
  $token = ($line -split "\?t=")[1].Trim()
  Start-Process ("http://localhost:$Port/?t=" + $token)
  Say "Opened Rilla in your browser as '$Name'."
}

function Run-Rilla { docker compose exec -T api python -m app.cli @args }

switch ($Command.ToLower()) {
  "up" {
    Need-Docker
    if (-not (Test-Path ".env")) {
      Copy-Item ".env.example" ".env"
      Write-Host "Created .env. Fill in POSTGRES_PASSWORD and GROQ_API_KEY, save, close Notepad, then run  .\rilla up  again." -ForegroundColor Yellow
      notepad ".env"; exit 1
    }
    $env_text = Get-Content ".env" -Raw
    if ($env_text -notmatch "(?m)^POSTGRES_PASSWORD=\S+") { Fail "POSTGRES_PASSWORD is empty in .env. Set any long random text and run again." }
    if ($env_text -notmatch "(?m)^GROQ_API_KEY=\S+") { Write-Host "GROQ_API_KEY is empty in .env: reading will not work until you add it." -ForegroundColor Yellow }
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { Fail "Docker could not start Rilla. Run  .\rilla logs  to see why." }
    Wait-Ready
    Run-Rilla doctor
    Open-App
  }
  "open"    { Need-Docker; Open-App }
  "restart" { Need-Docker; docker compose restart api worker; Wait-Ready }
  "status"  { Need-Docker; docker compose ps; Run-Rilla status }
  "logs"    { Need-Docker; docker compose logs -f --tail 80 api worker }
  "down"    { Need-Docker; docker compose down }
  "demo"    { Need-Docker; Run-Rilla demo $Name; Open-App }
  "demo-clear" { Need-Docker; Run-Rilla demo $Name --clear }
  "batch"   { Need-Docker; Run-Rilla batch /app/eval/links.txt }
  "batch-force" { Need-Docker; Run-Rilla batch /app/eval/links.txt --force }
  "eval"    { Need-Docker; Run-Rilla eval }
  "eval-save" { Need-Docker; Run-Rilla eval --save }
  "read"    { Need-Docker; Run-Rilla read @Rest }
  "doctor"  { Need-Docker; Run-Rilla doctor }
  "test" {
    Need-Docker
    Say "Preparing the throwaway test database..."
    docker compose exec -T db psql -U rilla -d postgres -c "CREATE DATABASE rilla_test;" 2>$null | Out-Null
    docker compose --profile test run --rm tests
  }
  "public" {
    Need-Docker
    docker compose --profile public up -d tunnel
    Start-Sleep -Seconds 8
    $url = (docker compose logs tunnel 2>&1 | Select-String -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1)
    if ($url) { Say ("Public address for testers: " + $url.Matches[0].Value + "  (set PUBLIC_URL to it in .env so sign-in links use it)") }
    else { Write-Host "No address yet. Try again in a few seconds:  .\rilla public" -ForegroundColor Yellow }
  }
  default {
    Write-Host @"
Rilla commands
  .\rilla up          start everything, check it, open your browser signed in
  .\rilla open        open the app in your browser, signed in
  .\rilla demo        add sample places and offers so Places, Plan and You have data (then opens the app)
  .\rilla demo-clear  remove the sample data
  .\rilla status      what is running, counts, failed jobs, settings in use
  .\rilla logs        follow the live log (Ctrl+C to stop)
  .\rilla restart     restart the app and worker after a code change (no rebuild)
  .\rilla test        run all automated tests in Docker
  .\rilla batch       read the new links in eval\links.txt   (batch-force: redo all of them)
  .\rilla eval        score against the labelled cases      (eval-save: also keep the result)
  .\rilla read <link> see what Rilla hears, sees and extracts from one video
  .\rilla public      free public address so friends can test on their phones
  .\rilla down        stop everything (your data is kept)
Set RILLA_USER to open a different account (default: Suresh).
"@
  }
}
