# One-command control for Rila on Windows.   Usage:  .\rila <command>      (run  .\rila help  to list them)
param(
  [Parameter(Position = 0)][string]$Command = "help",
  [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest
)
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$Name = if ($env:RILA_USER) { $env:RILA_USER } elseif ($env:RILLA_USER) { $env:RILLA_USER } else { "Suresh" }   # whose account to open; set RILA_USER to change
$Port = 8000

function Say($m) { Write-Host $m -ForegroundColor Cyan }
function Fail($m) { Write-Host $m -ForegroundColor Red; exit 1 }

function Need-Docker {
  docker info *> $null
  if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop, wait until it says running, then try again." }
}

function Wait-Ready {
  Say "Waiting for Rila to start (the first time can take a minute)..."
  for ($i = 0; $i -lt 60; $i++) {
    try {
      $r = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$Port/api/health" -TimeoutSec 3
      if ($r.StatusCode -eq 200) { Say "Rila is up."; return }
    } catch { }
    Start-Sleep -Seconds 2
  }
  Write-Host "Rila did not answer in 2 minutes. Look at the log:  .\rila logs" -ForegroundColor Yellow
  exit 1
}

function Open-App {
  $out = docker compose exec -T api python -m app.cli link $Name
  $line = $out | Where-Object { $_ -match "\?t=" } | Select-Object -Last 1
  if (-not $line) { Fail "Could not get a sign-in link. Check:  .\rila status" }
  $token = ($line -split "\?t=")[1].Trim()
  Start-Process ("http://localhost:$Port/?t=" + $token)
  Say "Opened Rila in your browser as '$Name'."
}

function Run-Rila { docker compose exec -T api python -m app.cli @args }

switch ($Command.ToLower()) {
  "up" {
    Need-Docker
    if (-not (Test-Path ".env")) {
      Copy-Item ".env.example" ".env"
      Write-Host "Created .env. Fill in POSTGRES_PASSWORD and GROQ_API_KEY, save, close Notepad, then run  .\rila up  again." -ForegroundColor Yellow
      notepad ".env"; exit 1
    }
    $env_text = Get-Content ".env" -Raw
    if ($env_text -notmatch "(?m)^POSTGRES_PASSWORD=\S+") { Fail "POSTGRES_PASSWORD is empty in .env. Set any long random text and run again." }
    if ($env_text -notmatch "(?m)^GROQ_API_KEY=\S+") { Write-Host "GROQ_API_KEY is empty in .env: reading will not work until you add it." -ForegroundColor Yellow }
    if ($env_text -match "(?m)^BOT_TOKEN=\S+") {
      Say "BOT_TOKEN found: starting the Telegram bot too."
      docker compose --profile telegram up -d --build
    } else {
      docker compose up -d --build
    }
    if ($LASTEXITCODE -ne 0) { Fail "Docker could not start Rila. Run  .\rila logs  to see why." }
    if ($env_text -notmatch "(?m)^BOT_TOKEN=\S+") {
      docker compose --profile telegram rm -sf bot *> $null   # remove a bot container left from before: it cannot run without a token
    }
    Wait-Ready
    Run-Rila doctor
    Open-App
  }
  "open"    { Need-Docker; Open-App }
  "restart" {
    Need-Docker
    # recreate (not just restart) so changes to .env are picked up; the database is left alone
    docker compose up -d --force-recreate api worker
    Wait-Ready
    Open-App
  }
  "status"  { Need-Docker; docker compose ps; Run-Rila status }
  "logs"    { Need-Docker; docker compose logs -f --tail 80 api worker }
  "down"    { Need-Docker; docker compose down }
  "demo"    { Need-Docker; Run-Rila demo $Name; Open-App }
  "demo-clear" { Need-Docker; Run-Rila demo $Name --clear }
  "batch"   { Need-Docker; Run-Rila batch /app/eval/links.txt }
  "batch-force" { Need-Docker; Run-Rila batch /app/eval/links.txt --force }
  "eval"    { Need-Docker; Run-Rila eval }
  "eval-save" { Need-Docker; Run-Rila eval --save }
  "read"    { Need-Docker; Run-Rila read @Rest }
  "doctor"  { Need-Docker; Run-Rila doctor }
  "label"   { Need-Docker; docker compose exec api python -m app.cli label }
  "recs-eval" { Need-Docker; Run-Rila recs-eval $Name }
  "reembed" { Need-Docker; Run-Rila reembed }
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
    else { Write-Host "No address yet. Try again in a few seconds:  .\rila public" -ForegroundColor Yellow }
  }
  default {
    Write-Host @"
Rila commands
  .\rila up          start everything, check it, open your browser signed in
  .\rila open        open the app in your browser, signed in
  .\rila reembed     recompute search vectors after changing EMBED_MODEL in .env (run .\rila restart first)
  .\rila label       check the model's answers for each test video (Enter = right) so eval has real numbers
  .\rila recs-eval   replay your likes and visits to measure how well the taste model predicts them
  .\rila demo        add sample places and offers so Places, Plan and You have data (then opens the app)
  .\rila demo-clear  remove the sample data
  .\rila status      what is running, counts, failed jobs, settings in use
  .\rila logs        follow the live log (Ctrl+C to stop)
  .\rila restart     apply code or .env changes (recreates app and worker, keeps your data) and open the browser
  .\rila test        run all automated tests in Docker
  .\rila batch       read the new links in eval\links.txt   (batch-force: redo all of them)
  .\rila eval        score against the labelled cases      (eval-save: also keep the result)
  .\rila read <link> see what Rila hears, sees and extracts from one video
  .\rila public      free public address so friends can test on their phones
  .\rila down        stop everything (your data is kept)
Set RILA_USER to open a different account (default: Suresh).
"@
  }
}
