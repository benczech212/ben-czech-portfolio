#!/usr/bin/env bash
# Deploy a repo plugin (default: czs-blocks) to the staging VM. Safe to re-run.
#   1. zips wordpress/plugins/<plugin>/ into data/build/<plugin>.zip (no build step)
#   2. copies it to the VM (IAP tunnel)
#   3. php -l every file inside the wp-cli container; aborts on a syntax error
#   4. wp plugin install --force --activate, then flushes Elementor's CSS cache
#
# Usage: infra/wordpress/deploy-plugin.sh [plugin-slug] [--kit]
#   --kit   also run `wp czs-blocks apply-kit` (brand colors/fonts into Elementor Site Settings)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PLUGIN="czs-blocks"
KIT=0
for arg in "$@"; do
  case "$arg" in
    --kit) KIT=1 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) PLUGIN="$arg" ;;
  esac
done

# The slug is interpolated into paths and into the remote shell script below, so allow only
# WordPress-style slugs: ^[a-z0-9-]+$, spelled out so the locale can't widen the a-z range.
if ! [[ "$PLUGIN" =~ ^[abcdefghijklmnopqrstuvwxyz0123456789-]+$ ]]; then
  echo "invalid plugin slug: '$PLUGIN' (allowed: a-z, 0-9, -)" >&2; exit 2
fi

SRC="$ROOT/wordpress/plugins/$PLUGIN"
[ -f "$SRC/$PLUGIN.php" ] || { echo "no plugin at $SRC" >&2; exit 1; }
OUT="$ROOT/data/build"; mkdir -p "$OUT"
ZIP="$OUT/$PLUGIN.zip"

python - "$SRC" "$ZIP" "$PLUGIN" <<'PY'
import os, sys, zipfile
src, zp, name = sys.argv[1:]
n = 0
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    for dp, dns, fns in os.walk(src):
        dns[:] = sorted(d for d in dns if not d.startswith(".") and d != "__pycache__")
        for f in sorted(fns):
            if f.startswith("."):
                continue
            p = os.path.join(dp, f)
            z.write(p, (name + "/" + os.path.relpath(p, src)).replace(os.sep, "/"))
            n += 1
print(f"zipped {n} files -> {zp} ({os.path.getsize(zp)//1024} KB)")
PY

# shellcheck source=../gcp/config.sh
. "$ROOT/infra/gcp/config.sh"
LOCAL_ZIP="$ZIP"
command -v cygpath >/dev/null 2>&1 && LOCAL_ZIP="$(cygpath -w "$ZIP")"   # gcloud on Windows wants a Windows path

# Host keys: no --strict-host-key-checking override. gcloud pins the VM's key in
# ~/.ssh/google_compute_known_hosts (fetched from guest attributes, see infra/gcp/provision.sh)
# and refuses to connect if it changes.
gcloud compute scp --zone "$ZONE" --tunnel-through-iap "$LOCAL_ZIP" "$VM:"

# Remote script, shipped base64-encoded to avoid nested quoting.
REMOTE=$(cat <<EOF
set -euo pipefail
sudo install -m 644 "\$HOME/$PLUGIN.zip" "/tmp/$PLUGIN.zip"
rm -f "\$HOME/$PLUGIN.zip"
cd /opt/wordpress
sudo docker compose run --rm -T -v "/tmp/$PLUGIN.zip:/tmp/$PLUGIN.zip:ro" cli sh -c '
  set -e
  rm -rf /tmp/lint && mkdir -p /tmp/lint && cd /tmp/lint && unzip -q /tmp/$PLUGIN.zip
  for f in \$(find . -name "*.php"); do php -l "\$f" >/dev/null || { echo "PHP syntax error in \$f"; exit 1; }; done
  echo "php -l ok"
  cd /var/www/html
  wp plugin install /tmp/$PLUGIN.zip --force --activate
  if [ "$KIT" = 1 ]; then wp czs-blocks apply-kit; fi
  wp elementor flush_css
  wp option get blog_public | grep -qx 0 || echo "WARNING: blog_public is not 0 (site would be indexable)"
'
sudo rm -f "/tmp/$PLUGIN.zip"
EOF
)
B64=$(printf '%s' "$REMOTE" | base64 -w0)
gcloud compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap \
  --command "echo $B64 | base64 -d | bash" 2>&1 | grep -v '^ Container '
echo "deployed $PLUGIN"
