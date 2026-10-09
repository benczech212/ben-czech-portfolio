# Keeps Windows awake and the displays on for as long as this process runs, using the
# same API media players use (SetThreadExecutionState). Independent of power-plan settings,
# which a domain policy may reset. Launched detached by src/apps/keepalive.js.
param([string]$PidFile = "", [int]$IntervalSeconds = 30, [switch]$DisplayToo)
$ErrorActionPreference = 'Stop'
Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll", SetLastError = true)] public static extern uint SetThreadExecutionState(uint esFlags);'
$ES_CONTINUOUS = [uint32]2147483648      # 0x80000000 (hex literal would parse as a negative Int32 in PS 5.1)
$ES_SYSTEM_REQUIRED = [uint32]1
$ES_DISPLAY_REQUIRED = [uint32]2
$flags = $ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED
if ($DisplayToo) { $flags = $flags -bor $ES_DISPLAY_REQUIRED }
if ($PidFile) { Set-Content -Path $PidFile -Value $PID -Encoding ASCII }
try {
  while ($true) {
    [void][Win32.Power]::SetThreadExecutionState($flags)   # re-assert periodically; harmless if unchanged
    Start-Sleep -Seconds $IntervalSeconds
  }
} finally {
  [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS)
  if ($PidFile -and (Test-Path $PidFile)) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
}
