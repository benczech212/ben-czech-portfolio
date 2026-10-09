# Fullscreen black window on every display; the message + a hidden button on the
# message screen. Launched detached by src/apps/screens.js. Closing (button, taskkill)
# ends the process. If -OnHide is given, the button also runs that command.
param(
  [string]$Message = "This is an exhibit computer. Please do not touch.",
  [string]$SubMessage = "",
  [string]$MessageScreen = "primary",     # primary | \\.\DISPLAY1 | index (0-based)
  [string]$OnHide = "",                   # command line to run when the hidden button is clicked
  [string]$PidFile = "",
  [int]$ButtonSize = 80,
  [string]$Title = "Exhibit Paused",      # window title (used to find the windows)
  [switch]$OnlyMessageScreen,             # cover just the message screen, not every display
  [string]$ConfigFile = ""                # JSON with the same keys (used by screens.js to avoid quoting issues)
)
$ErrorActionPreference = 'Stop'
if ($ConfigFile -and (Test-Path $ConfigFile)) {
  $c = Get-Content $ConfigFile -Raw | ConvertFrom-Json
  foreach ($k in 'Message', 'SubMessage', 'MessageScreen', 'OnHide', 'PidFile', 'Title') { if ($null -ne $c.$k -and "$($c.$k)" -ne '') { Set-Variable -Name $k -Value "$($c.$k)" } }
  if ($null -ne $c.ButtonSize) { $ButtonSize = [int]$c.ButtonSize }
  if ($c.OnlyMessageScreen -eq $true) { $OnlyMessageScreen = $true }
  # Preferred form: executable + argument list (no shell quoting involved).
  $OnHideExe = ""; $OnHideArgs = @()
  if ($c.OnHideExe) { $OnHideExe = "$($c.OnHideExe)"; $OnHideArgs = @($c.OnHideArgs | ForEach-Object { "$_" }) }
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
if ($PidFile) { Set-Content -Path $PidFile -Value $PID -Encoding ASCII }

$screens = [System.Windows.Forms.Screen]::AllScreens
$msgIndex = -1
for ($i = 0; $i -lt $screens.Count; $i++) {
  $s = $screens[$i]
  if ($MessageScreen -eq 'primary' -and $s.Primary) { $msgIndex = $i }
  elseif ($MessageScreen -eq $s.DeviceName) { $msgIndex = $i }
  elseif ($MessageScreen -match '^\d+$' -and [int]$MessageScreen -eq $i) { $msgIndex = $i }
}
if ($msgIndex -lt 0) { $msgIndex = 0 }

$forms = New-Object System.Collections.ArrayList
$script:closing = $false
$hideAll = {
  if ($script:closing) { return }
  $script:closing = $true
  foreach ($f in @($forms)) { try { $f.Close() } catch {} }
  try {
    if ($OnHideExe) { Start-Process -FilePath $OnHideExe -ArgumentList $OnHideArgs -WindowStyle Hidden }
    elseif ($OnHide) { Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $OnHide -WindowStyle Hidden }
  } catch {}
}

for ($i = 0; $i -lt $screens.Count; $i++) {
  if ($OnlyMessageScreen -and $i -ne $msgIndex) { continue }
  $s = $screens[$i]
  $f = New-Object System.Windows.Forms.Form
  $f.Text = $Title
  $f.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
  $f.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
  $f.ShowInTaskbar = $false
  $f.TopMost = $true
  $f.BackColor = [System.Drawing.Color]::Black
  $f.Location = New-Object System.Drawing.Point($s.Bounds.X, $s.Bounds.Y)
  $f.Size = New-Object System.Drawing.Size($s.Bounds.Width, $s.Bounds.Height)
  $f.Add_Shown({ $this.Location = $this.Tag.Location; $this.Size = $this.Tag.Size; $this.Activate() })
  $f.Tag = [pscustomobject]@{ Location = $f.Location; Size = $f.Size }

  if ($i -eq $msgIndex) {
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Dock = [System.Windows.Forms.DockStyle]::Fill
    $lbl.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
    $lbl.ForeColor = [System.Drawing.Color]::FromArgb(220, 220, 220)
    $lbl.BackColor = [System.Drawing.Color]::Black
    $lbl.Font = New-Object System.Drawing.Font("Segoe UI", 34, [System.Drawing.FontStyle]::Regular)
    $lbl.Text = $Message
    if ($SubMessage) { $lbl.Text = $Message + "`n`n" + $SubMessage }
    $f.Controls.Add($lbl)

    # Hidden button: bottom-right corner, black on black, no border, no hover highlight.
    $btn = New-Object System.Windows.Forms.Button
    $btn.Size = New-Object System.Drawing.Size($ButtonSize, $ButtonSize)
    $btn.Location = New-Object System.Drawing.Point(($s.Bounds.Width - $ButtonSize), ($s.Bounds.Height - $ButtonSize))
    $btn.Anchor = [System.Windows.Forms.AnchorStyles]::Bottom -bor [System.Windows.Forms.AnchorStyles]::Right
    $btn.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
    $btn.FlatAppearance.BorderSize = 0
    $btn.FlatAppearance.MouseOverBackColor = [System.Drawing.Color]::Black
    $btn.FlatAppearance.MouseDownBackColor = [System.Drawing.Color]::Black
    $btn.BackColor = [System.Drawing.Color]::Black
    $btn.ForeColor = [System.Drawing.Color]::Black
    $btn.Text = ""
    $btn.TabStop = $false
    $btn.Cursor = [System.Windows.Forms.Cursors]::Default
    $btn.Add_Click($hideAll)
    $f.Controls.Add($btn)
    $btn.BringToFront()
  } else {
    $f.Cursor = [System.Windows.Forms.Cursors]::Default
  }
  [void]$forms.Add($f)
}

# Show the secondary screens first, then run the message loop on the message screen.
$main = $null
foreach ($f in @($forms)) { if ($f.Controls.Count -gt 0) { $main = $f } }
if ($null -eq $main) { $main = $forms[0] }
foreach ($f in @($forms)) { if ($f -ne $main) { $f.Show() } }
$main.Add_FormClosed({ foreach ($f in @($forms)) { if ($f -ne $main) { try { $f.Close() } catch {} } } })
try {
  [System.Windows.Forms.Application]::Run($main)
} finally {
  if ($PidFile -and (Test-Path $PidFile)) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
}
