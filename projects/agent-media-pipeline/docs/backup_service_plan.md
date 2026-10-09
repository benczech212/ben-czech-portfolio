# Backup service: plan (not started)

Drafted 2026-09-29. Build after the failing-drive rescue and the media reorganization are done.

## Goal
One always-on service that backs up chosen folders to the NAS and Google Drive. You add and manage those folders in
the dashboard. It replaces the one-off scripts written so far and the NAS vendor's desktop backup app.

## What gets backed up where
| Content | Lives on | NAS | Google Drive |
|---|---|---|---|
| TV / Movies (Plex) | NAS Media | is the home | **never** (excluded by rule) |
| Music (Plex) | NAS Media | is the home | mirror |
| Audiobooks / Books | NAS Media | is the home | no |
| Creative media: client library, stock footage, sound effects, VJ loops, assets | PC media drive | mirror | mirror |
| Current projects, documents, notes, code | PC work drive | snapshot | snapshot |
| Personal archive (old backups, past projects, recordings) | PC archive folder | snapshot | snapshot |
| AI chats, app settings, WSL home | PC system drive | snapshot (+ monthly WSL export) | snapshot |
| Plex database | NAS | is the home | snapshot |

The PC backs up to the NAS; the NAS (always on) uploads to Google Drive overnight.

## Two kinds of backup job
| Kind | For | How | Why |
|---|---|---|---|
| **Snapshot** (versioned) | personal files, projects, AI chats, WSL home | restic: deduplicated, encrypted, keeps history | A deleted or ransomware-encrypted file can't overwrite the good copy; you can restore any earlier day |
| **Mirror** | client media library, Music library | rclone copy (never deletes on the other side unless told to) | Files stay browsable as normal files on NAS/Drive; no versioning needed for media |

Every job has: source paths, include/exclude patterns, destinations (NAS, Drive, or both), schedule, retention,
bandwidth/IO limits.

## Architecture
- **Engine:** a Python service (same style as the dashboard) that runs restic and rclone and records every run in a small SQLite DB.
- **Runs as:** a Windows service via NSSM, or a startup scheduled task, running under your user account.
  - Gotcha: mapped drive letters don't exist for services. Use `\\<nas>\<share>\...` UNC paths or SFTP to the NAS.
- **Secrets:** restic password and rclone config go in Windows Credential Manager or an encrypted rclone config, never in job files.
  - Needs your own Google OAuth client ID for rclone (`setup/set_rclone_client_id.ps1`); the shared rclone one is rate-limited.
- **UI:** a new "Backups" tab in the dashboard:
  - Add a job with a folder picker, choose destinations, schedule and retention.
  - Run now, pause, and see history (last success, size, files changed, errors).
  - Browse snapshots and restore a file or folder to a chosen location (never over the original by default).
- **Safety rules learned from the failing-drive rescue:**
  - Never write to or delete from a source.
  - Throttle reads.
  - Watch the Windows disk error events (event 153) and SMART. If a source disk starts failing, alert and switch that job to rescue-priority mode.
- **Checks:**
  - Weekly `restic check` plus a random read of 5% of the data.
  - Monthly automatic test restore of a few files, compared by hash.
  - Dashboard alert if any job hasn't succeeded in N days.
- **Google Drive limits:** 750 GB/day upload cap (`--drive-stop-on-upload-limit`, resume next day), and a nightly upload window so it doesn't eat the connection.

## Presets
### AI chat sessions
| Source | Notes |
|---|---|
| `%USERPROFILE%\.claude\projects` | Claude Code transcripts |
| `%USERPROFILE%\.claude` (rest) | settings, memory, skills; exclude caches |
| Other AI tools' folders under `%USERPROFILE%` | keep history and config; skip embedded browser profiles and backup copies (often most of the size) |
| `%APPDATA%\Code\User\workspaceStorage` | VS Code / Copilot chat history |
| WSL `~/.claude`, `~/.codex`, etc. | check inside WSL when building |

- Snapshot job only (encrypted), because transcripts can contain secrets.
- Web chats (claude.ai, ChatGPT, Gemini) have no local files. Use their "Export data" feature, and the service watches Downloads for export zips and files them into the backup.

### WSL
- Don't copy the live `ext4.vhdx`: it's the whole disk image every time and inconsistent while WSL runs.
- **Daily:** restic snapshot from inside WSL of `/home/<user>` and `/etc`, excluding `node_modules`, `.cache`, virtualenvs and Docker data. Only changes are sent, so it's small.
- **Monthly:** full `wsl --export <distro>` to the NAS at night (briefly stops WSL), keeping the last 2 for a one-command full restore.
- `docker-desktop`: skip; images can be rebuilt.
- Review, then retire, the NAS vendor app's WSL backup job.

## Build order
1. Job config format and SQLite history; restic snapshot to NAS; one manual job (AI chats) to prove restore works.
2. Backups tab: add/edit jobs, run now, history.
3. Google Drive destination (restic via the rclone backend for snapshots; rclone copy for mirrors); upload window and daily cap.
4. Presets: AI chats, WSL daily and monthly export, personal folders.
5. Service install, alerts, checks and test restores; restore UI.
6. Move the client library and Music to mirror jobs (TV/Movies stay NAS-only); turn off the overlapping vendor-app jobs.

## Decide later
- restic plus our own UI (one dashboard, full control) vs. Kopia (has its own UI and scheduler, less work, separate app).
  The NAS side already runs Kopia (`nas/docker/kopia/compose.yaml`) with a nightly offsite copy to Drive.
- Retention, e.g. 7 daily / 4 weekly / 12 monthly.
- Upload hours and bandwidth cap.
- Whether the NAS gets a second disk (RAID1) before relying on it as the main backup.
