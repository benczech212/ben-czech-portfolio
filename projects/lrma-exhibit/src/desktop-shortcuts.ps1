# Creates desktop shortcuts (.lnk) for the operator .bat files. Run via "Install Desktop Shortcuts.bat".
param([string]$ShortcutsDir = (Join-Path $PSScriptRoot "..\shortcuts"), [string]$Prefix = "Exhibit - ")
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell
$map = @(
  @{ file = 'Boot.bat';                name = 'Boot';               icon = 'shell32.dll,137' },
  @{ file = 'Pause Exhibit.bat';       name = 'Pause';              icon = 'shell32.dll,109' },
  @{ file = 'Wake Exhibit.bat';        name = 'Wake';               icon = 'shell32.dll,138' },
  @{ file = 'Show Control Panel.bat';  name = 'Control Panel';      icon = 'shell32.dll,21' },
  @{ file = 'Status.bat';              name = 'Status';             icon = 'shell32.dll,23' },
  @{ file = 'Restart.bat';             name = 'Restart Apps';       icon = 'shell32.dll,238' },
  @{ file = 'Shutdown.bat';            name = 'Stop Apps';          icon = 'shell32.dll,27' }
)
foreach ($m in $map) {
  $target = Join-Path (Resolve-Path $ShortcutsDir) $m.file
  if (-not (Test-Path $target)) { Write-Warning "missing $target"; continue }
  $lnk = Join-Path $desktop ("$Prefix$($m.name).lnk")
  $s = $ws.CreateShortcut($lnk)
  $s.TargetPath = $target
  $s.WorkingDirectory = Split-Path $target
  $s.IconLocation = "%SystemRoot%\System32\$($m.icon)"
  $s.Description = "Exhibit: $($m.name)"
  $s.Save()
  "created $lnk"
}
