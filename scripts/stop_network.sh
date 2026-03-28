#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK_DIR="$ROOT_DIR/.network"

echo "Stopping network..."

# 1) Try PIDs first if the file exists
if [ -f "$NETWORK_DIR/pids.txt" ]; then
  echo "Using .network/pids.txt..."
  while read -r pid; do
    if [ -n "${pid:-}" ]; then
      kill "$pid" 2>/dev/null || true
      taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    fi
  done < "$NETWORK_DIR/pids.txt"
else
  echo "No .network/pids.txt found"
fi

# 2) Fallback for Git Bash / Windows:
# kill all Python processes whose command line contains src.cli.run_node
echo "Searching for remaining run_node processes..."

powershell.exe -NoProfile -Command '
$procs = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and $_.CommandLine -match "src\.cli\.run_node"
}
if (-not $procs) {
  Write-Output "No remaining run_node processes found"
  exit 0
}
$procs | ForEach-Object {
  Write-Output ("Killing PID " + $_.ProcessId + " :: " + $_.CommandLine)
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
' || true

rm -f "$NETWORK_DIR/pids.txt"
echo "Stopped network."