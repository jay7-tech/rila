# Removes the old prototype that the new app/ package replaces.
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\cleanup_old.ps1
# Safe to run more than once. chroma_db (old vector store) is not tracked by git.
$targets = @("src", "migrations", "chroma_db")
Write-Host "This will delete (if present): $($targets -join ', ')"
Write-Host "Old saved places in the previous database are NOT migrated."
$ok = Read-Host "Type YES to continue"
if ($ok -ne "YES") { Write-Host "Cancelled."; exit 0 }
foreach ($t in $targets) {
  if (-not (Test-Path $t)) { Write-Host "Already gone: $t"; continue }
  $tracked = git ls-files $t 2>$null
  if ($tracked) { git rm -r -q -f $t 2>$null }
  if (Test-Path $t) { Remove-Item -Recurse -Force $t }
  Write-Host "Removed $t"
}
Get-ChildItem -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -notmatch "\\\.venv\\" } |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Done. Check with: git status"
