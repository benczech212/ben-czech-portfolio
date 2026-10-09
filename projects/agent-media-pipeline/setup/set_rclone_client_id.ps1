# Switch rclone's "gdrive" remote (on this PC and on the NAS) from rclone's shared Google client ID to your own.
#
# Before running:
#   1. Create the client ID in Google Cloud Console (Desktop app type) and click "Publish app" on the OAuth consent
#      screen, so the sign-in doesn't expire every 7 days.
#   2. Turn SSH on for the NAS, with key-based login for your NAS user.
#   3. The script asks for the client ID and secret; nothing is saved in this file.
#
# Run it in your own PowerShell window (it opens a browser for the sign-in and asks for the secret):
#   powershell -ExecutionPolicy Bypass -File .\setup\set_rclone_client_id.ps1 [-NasHost <ssh host>] [-NasRclone <path>]
#
# NasHost defaults to $env:NAS_SSH_HOST, else "nas" (an ssh host name or an alias from ~/.ssh/config).
# NasRclone is rclone's path on the NAS (default ~/bin/rclone).
#
# What it does: one Google sign-in in your browser, then it saves the new ID, secret and sign-in token to the
# "gdrive" remote on the PC and on the NAS, and checks both. Nothing is printed to the screen or sent anywhere else.
param(
    [string]$NasHost = $(if ($env:NAS_SSH_HOST) { $env:NAS_SSH_HOST } else { 'nas' }),
    [string]$NasRclone = '~/bin/rclone'
)

# 'Continue': rclone prints notices on stderr (e.g. about the redirect URL), which must not stop the script.
# Failures are checked explicitly below instead.
$ErrorActionPreference = 'Continue'
$rc = (Get-Command rclone -ErrorAction SilentlyContinue).Source
if (-not $rc) { $rc = "$env:LOCALAPPDATA\Microsoft\WinGet\Links\rclone.exe" }
if (-not (Test-Path $rc)) { throw "rclone not found; install it (winget install Rclone.Rclone) or put it on PATH." }

$ClientId = Read-Host "Client ID"
$secure = Read-Host "Client secret" -AsSecureString
$secretText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
if (-not $ClientId -or -not $secretText) { throw "Client ID and secret are both needed." }

# 1. One sign-in: rclone opens your browser. Pick the Google account that owns the Drive.
#    Google warns the app isn't verified - it's yours: Advanced -> Go to (app name).
Write-Host "`nOpening your browser for the Google sign-in..." -ForegroundColor Cyan
$out = & $rc authorize "drive" $ClientId $secretText 2>&1 | Out-String
$m = [regex]::Match($out, '(?s)--->\s*(\{.*?\})\s*<---')
if (-not $m.Success) { throw "Sign-in didn't finish; no token came back. Run the script again." }
$token = $m.Groups[1].Value

# 2. This PC
# Windows PowerShell 5.1 drops the " inside arguments passed to programs, so the token's quotes are escaped
& $rc config update gdrive client_id $ClientId client_secret $secretText token ($token -replace '"', '\"') --non-interactive | Out-Null
Write-Host "PC: gdrive updated." -ForegroundColor Green

# 3. The NAS - the values go over SSH as input, not on a command line, so they don't show in its process list
#     (no quotes in this command: the values contain no spaces, and quotes would be mangled on the way to ssh)
$nasCmd = "read -r id; read -r sec; read -r tok; $NasRclone config update gdrive client_id `$id client_secret `$sec token `$tok --non-interactive >/dev/null && echo NAS-OK"
$res = "$ClientId`n$secretText`n$token`n" | ssh -o ConnectTimeout=10 $NasHost $nasCmd
if ($res -match 'NAS-OK') { Write-Host "NAS: gdrive updated." -ForegroundColor Green }
else { Write-Warning "NAS not updated (is SSH on?). Turn SSH on and run the script again; the PC part is harmless to repeat." }

Remove-Variable secretText, secure, token, out, m -ErrorAction SilentlyContinue

# 4. Check both. The "shared Google Drive client_id" notice should be gone.
Write-Host "`nChecking the PC..." -ForegroundColor Cyan
$pc = & $rc about gdrive: 2>&1 | Out-String
if ($pc -match 'shared Google Drive client_id') { Write-Warning "PC still uses the shared client ID." }
elseif ($pc -match 'Total:') { Write-Host "PC OK: using your own client ID." -ForegroundColor Green } else { Write-Warning $pc }

if ($res -match 'NAS-OK') {
    Write-Host "Checking the NAS..." -ForegroundColor Cyan
    $n = ssh $NasHost "$NasRclone about gdrive: 2>&1" | Out-String
    if ($n -match 'shared Google Drive client_id') { Write-Warning "NAS still uses the shared client ID." }
    elseif ($n -match 'Total:') { Write-Host "NAS OK: using your own client ID." -ForegroundColor Green } else { Write-Warning $n }
}

Write-Host "`nDone." -ForegroundColor Yellow
