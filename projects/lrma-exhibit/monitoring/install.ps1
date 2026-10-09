# Extracts the downloaded Prometheus / Loki / Grafana zips from C:\dev\tools\monitoring\dl into
# C:\dev\tools\monitoring\{prometheus,loki,grafana} and creates the data folders. No admin needed.
# Downloads (run first, or let this script fetch them):
#   prometheus.zip  https://github.com/prometheus/prometheus/releases (prometheus-<v>.windows-amd64.zip)
#   loki.zip        https://github.com/grafana/loki/releases (loki-windows-amd64.exe.zip)
#   grafana.zip     https://dl.grafana.com/oss/release/grafana-<v>.windows-amd64.zip
param([string]$Root = "C:\dev\tools\monitoring", [string]$PrometheusVersion = "3.14.0", [string]$LokiVersion = "3.7.7", [string]$GrafanaVersion = "13.2.1")
$ErrorActionPreference = 'Stop'
$dl = Join-Path $Root 'dl'
New-Item -ItemType Directory -Force $dl | Out-Null
foreach ($d in 'data\prometheus', 'data\loki', 'data\grafana') { New-Item -ItemType Directory -Force (Join-Path $Root $d) | Out-Null }

$items = @(
  @{ zip = 'prometheus.zip'; url = "https://github.com/prometheus/prometheus/releases/download/v$PrometheusVersion/prometheus-$PrometheusVersion.windows-amd64.zip"; dest = 'prometheus'; inner = "prometheus-$PrometheusVersion.windows-amd64"; check = 'prometheus.exe' },
  @{ zip = 'loki.zip';       url = "https://github.com/grafana/loki/releases/download/v$LokiVersion/loki-windows-amd64.exe.zip";                       dest = 'loki';       inner = $null;                                     check = 'loki-windows-amd64.exe' },
  @{ zip = 'grafana.zip';    url = "https://dl.grafana.com/oss/release/grafana-$GrafanaVersion.windows-amd64.zip";                                       dest = 'grafana';    inner = "grafana-$GrafanaVersion";                check = 'bin\grafana.exe' }
)
foreach ($it in $items) {
  $zip = Join-Path $dl $it.zip
  $dest = Join-Path $Root $it.dest
  if (Test-Path (Join-Path $dest $it.check)) { "$($it.dest): already installed"; continue }
  if (-not (Test-Path $zip)) { "downloading $($it.url)"; Invoke-WebRequest -Uri $it.url -OutFile $zip }
  $tmp = Join-Path $Root ("_x_" + $it.dest)
  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
  "extracting $($it.zip)"
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
  if ($it.inner) { Move-Item (Join-Path $tmp $it.inner) $dest } else { Move-Item $tmp $dest }
  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
  if (Test-Path (Join-Path $dest $it.check)) { "$($it.dest): installed" } else { Write-Warning "$($it.dest): $($it.check) not found after extraction" }
}
"done. Enable the prometheus/loki/grafana apps in exhibit.yaml and run: node bin/exhibit.js run monitoring"
