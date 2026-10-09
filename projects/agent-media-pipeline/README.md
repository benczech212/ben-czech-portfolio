Part of Ben Czech's portfolio. Built with Claude Code.

# Agent media pipeline

An agent-directed cleanup of a multi-terabyte home media library, plus the backup plan that follows it. Claude Code (an AI
coding agent) wrote and ran the scripts here; every destructive step waited for a human decision made in a local
dashboard, and every change was logged so it could be checked or undone.

What it covers:

- **Review:** a local web dashboard ("Control Room") reads a backup copy of the Plex database and suggests keep or
  drop for every movie and show, with the reason. Duplicates are judged the way Plex matched them, then checked
  against the file name's episode numbers and the running time. Nothing counts until a person marks it.
- **Clean-up:** approved drops and parked duplicates are deleted by scripts that run as a dry run unless given `--go`.
- **Re-encode:** kept titles that are too large or in a poor streaming format are re-encoded on the GPU to one
  standard (HEVC 10-bit MKV, at most 1080p and 2.5 GB per hour) and verified before they replace anything.
- **Naming:** files are renamed to Plex's naming standard, automatically only when Plex's match and the file name
  agree.
- **Backups:** Docker compose files for the NAS (Plex, a Kopia backup server with a nightly offsite copy to Google
  Drive, and an rclone uploader), and a written plan for a backup service (`docs/backup_service_plan.md`).

## Results

Verified from the run's logs:

- **7,429 files (~2.85 TB) removed**, including 2,828 duplicates (725 GB) and 737 files of titles dropped in the
  Plex review (697 GB).
- **GPU re-encode of 251 titles: 246 GB → 127 GB.** Each one was checked for duration, streams and a full decode,
  and scored with VMAF on 3 samples (lowest 70.4, median 94.5; the pass mark is 70).
- **335 clips converted to DXV** (a video-performance codec), with each original deleted only after its converted
  twin was verified.
- **Renames only when Plex's match agrees** with the file name; each rename is logged as a batch with its sidecar
  files and can be undone from the dashboard.

The drive-rescue scripts and the DXV conversion scripts are not included in this copy. The dashboard still shows
the conversion's progress if its queue file is present, and `client_filter.py` and `build_review_data.py` read
that step's output.

## The lesson

**The problem.** You want an AI coding agent to do real, destructive work on data you care about: deleting
thousands of files, replacing originals with re-encodes, renaming a library. Asking the model to "be careful"
isn't a control. One wrong path, a truncated encode or a bad match costs data you may not be able to get back.

**The idea.** Split the work so the agent proposes and code enforces. The agent writes and runs scripts that
build a review. A human makes the decisions in a dashboard. The scripts that act are dry runs by default,
check every input against an allow-list before touching anything, verify results before replacing, and log
each change so it can be checked or undone. The safety lives in code paths the model can't talk its way past.

### Walkthrough

1. **Decisions are data, made by a person.** [`dashboard/build_plex_review.py`](dashboard/build_plex_review.py)
   only *suggests* keep or drop, with a reason. The dashboard saves your marks to `decisions.json`, and
   `delete_approved.py` reads them. A duplicate marked anything other than "remove" (for example "keep
   both") is skipped, whatever the review suggested.
2. **Dry run unless told otherwise:** [`dashboard/delete_approved.py`](dashboard/delete_approved.py). Without
   `--go` it does every check, prints what it would delete and why it skipped the rest, then stops. Before
   that, it re-checks each file: still the size the review saw, and its kept twin still present.
3. **Check the whole list before deleting anything.** One path outside the parking folder stops the run before
   a single file is removed:
   ```python
   bad = [t for t in todo if not os.path.normcase(os.path.normpath(t[0])).startswith(SAFE_ROOTS)]
   if bad:   # not an assert: python -O would skip it
       sys.exit(f"refusing: paths outside {DUPDIR}: {bad[:3]}")
   ```
   [`dashboard/delete_plex_drops.py`](dashboard/delete_plex_drops.py) does the same against the library roots
   the review was built from, and never touches the failing drive.
4. **Verify before you replace:** [`dashboard/reencode_worker.py`](dashboard/reencode_worker.py). An encode is
   written as `.partial.mkv` and checked for length, audio track count, resolution, size per hour, a clean full
   decode, and VMAF on three samples. Only then does `os.replace(partial, dest)` swap it in. Any problem deletes
   the partial file and marks the job failed. The original source is only ever read.
5. **Make changes undoable:** [`dashboard/media_names.py`](dashboard/media_names.py). A file is renamed
   automatically only when Plex's match agrees with the file name. Each rename moves the video and its sidecar
   files as one logged batch in `rename_log.csv`, which the dashboard can undo.
6. **Keep the control surface local:** [`dashboard/server.py`](dashboard/server.py) binds to 127.0.0.1 and
   rejects foreign Host and Origin headers, so another web page can't press the buttons for you (see Security
   notes).

### Try it

The delete script's gates can be run on a few throwaway files without a NAS or Plex. Work in a temp folder,
because the script writes `deleted_log.csv` next to itself:

```bash
mkdir /tmp/try && cd /tmp/try
cp -r <repo>/projects/agent-media-pipeline/dashboard .
mkdir parked library
printf aaaa > library/film.mkv; printf aaaa > "parked/film (copy).mkv"; printf aaaa > outside.mkv
export DUPES_DIR="$PWD/parked"
python - <<'EOF'
import json, os; d = os.getcwd()
pair = lambda dup, n: {"dup": os.path.join(d, dup), "kept": os.path.join(d, "library", "film.mkv"), "bytes": n, "note": ""}
json.dump({"pairs": [pair("parked/film (copy).mkv", 4)]}, open("dashboard/dupes_review.json", "w"))
json.dump({}, open("dashboard/decisions.json", "w"))
EOF
python dashboard/delete_approved.py        # dry run: "to delete: 1 files ... dry run - nothing deleted"
```

Next, add `pair("outside.mkv", 4)` to the list and run it again. It stops with `refusing: paths outside ...`
and deletes nothing. Mark the parked file `{"dupes": {"<its full path>": "keep both"}}` in `decisions.json`
and it's skipped. With the original list, `--go` deletes the parked copy, keeps the library copy, and logs
one row to `dashboard/deleted_log.csv`.

### Take it to your own project

- Let the agent build the plan and the evidence; make a person's decision a file the action scripts read.
- Default every destructive script to a dry run. The flag that acts should be explicit and boring (`--go`).
- Validate the whole batch against an allow-list before the first change, not per item as you go.
- Write to a temp name, verify, then rename into place. Never overwrite the only good copy.
- Log every change in a form you can replay backwards. "Undo" is a feature, not an apology.

## Safety design

- **Dry run by default.** The delete scripts list what they would remove and why anything was skipped; only
  `--go` deletes.
- **CSV logs.** Every deletion goes to `deleted_log.csv` (time, path, size, reason), every rename to
  `rename_log.csv` (as an undoable batch).
- **Quarantine, then approval.** Duplicates and DXV-replaced originals were first moved to a parking folder and
  deleted only after review. A duplicate is deleted only if its kept twin is still present at the same size.
- **Delete allow-lists.** `delete_approved.py` refuses to run if any path is outside the parking folder;
  `delete_plex_drops.py` refuses if any path is outside the Plex library folders the review was built from (or
  their rescued copies), and never touches the failing drive. Both check the whole list before deleting anything.
- **Atomic writes.** Copies are written as `.partial`, size-checked, then renamed; encodes are written as
  `.partial.mkv` and renamed only after every check passes. State files are written to a temp file and swapped in.
- **Verification.** Each encode is checked for length, audio tracks, resolution, size cap, a full decode with no
  errors, and a VMAF score of at least 70 on three samples (`docs/media_pipeline.md`). Original sources are only
  ever read.
- **One worker, resumable.** A single-instance lock, a cross-process queue lock, and a saved state before and after
  every step; jobs left running by a crash go back to the queue and their partial output is removed.
- **Polite scheduling.** The encoder runs only while the PC is idle, after a cancellable countdown, and pauses the
  moment someone uses the PC.

## Layout

| Path | What |
|---|---|
| `dashboard/server.py` | The local dashboard (http://localhost:8765): overview, re-encode queue, library names, Plex review, backup plan |
| `dashboard/index.html` | Its single-page UI |
| `dashboard/config.py`, `config.example.json` | All paths and addresses (see Configuration) |
| `dashboard/build_plex_review.py` | Builds `plex_review.json` from a backup copy of the Plex database: suggestions, duplicates, re-encode needs |
| `dashboard/stage_media.py` | Places each kept title at its final name in the NAS library (`plan`, then `run`) |
| `dashboard/reencode_common.py` | Queue file, cross-process lock, format standard |
| `dashboard/reencode_worker.py` | The GPU re-encode worker: encode, verify, replace |
| `dashboard/build_encode_preview.py` | Before/after frames, sizes and VMAF for the encoder settings on test clips |
| `dashboard/media_names.py` | Naming check and renames from Plex's match, with undo |
| `dashboard/delete_plex_drops.py` | Deletes files of titles marked Drop (dry run unless `--go`) |
| `dashboard/delete_approved.py` | Deletes approved parked duplicates and DXV-replaced originals (dry run unless `--go`) |
| `dashboard/build_review_data.py` | Builds the duplicate/DXV review lists from the earlier steps' output |
| `dashboard/check_library_dupes.py` | Fresh duplicate scan of the client library (size, then sampled hash, then full hash); read-only |
| `dashboard/client_filter.py` | Writes the NAS uploader's rclone filter so clips go to Drive only once converted |
| `dashboard/start_dashboard.bat` | Starts the dashboard and opens it |
| `docs/media_pipeline.md` | The format standard, checks and naming rules |
| `docs/backup_service_plan.md` | Plan for the backup service (not built yet) |
| `nas/docker/*/compose.yaml` | Plex, Kopia (+ offsite copy), rclone uploader |
| `setup/set_rclone_client_id.ps1` | Switches rclone's Google Drive remote on the PC and NAS to your own OAuth client ID |

Requirements: Windows (the dashboard uses Windows APIs for idle detection, file locks and Explorer), Python 3.10+,
`plexapi` (`pip install plexapi`), an ffmpeg build with `hevc_nvenc`, `libvmaf` and `zscale`, an NVIDIA GPU, and
rclone. The dashboard and scripts keep their state (queue, decisions, logs) as JSON/CSV files next to themselves;
`.gitignore` keeps those out of the repository.

## Configuration

`dashboard/config.py` reads each key from an environment variable of the same name, then from
`dashboard/config.json` (copy `config.example.json`; or point `MEDIA_PIPELINE_CONFIG` at another file), then a
placeholder default. The defaults are placeholders: `N:\` for the NAS data share mapped as a drive letter,
`\\nas\docker` for the NAS's docker share, `nas.local` for the NAS's host name.

| Key | Meaning |
|---|---|
| `DASHBOARD_PORT` | Dashboard port (8765) |
| `WORK_DIR` | Scratch folder: Plex database copy, preview clips, other steps' output |
| `FFMPEG_DIR` | Folder with `ffmpeg.exe` / `ffprobe.exe` (blank: from PATH) |
| `RCLONE` | rclone executable (blank: from PATH) |
| `NAS_DATA` | The NAS data share as seen from the PC |
| `MEDIA_ROOT` | The NAS library holding `Movies\` and `TV Shows\` |
| `NAS_RECYCLE` | The NAS recycle bin (its size is shown on the overview) |
| `NAS_DOCKER_SHARE` | The NAS's docker share, where the compose projects keep their files |
| `CLIENT_LIBRARY` | The client media library mirrored to Google Drive |
| `CLIENT_LIBRARY_REMOTE` | Its rclone destination, e.g. `gdrive:Client Media Library` |
| `UPLOAD_FILTER`, `UPLOAD_LOG`, `OFFSITE_LOG` | The uploader's filter file and log, and the Kopia offsite log |
| `DUPES_DIR` | Parking folder for duplicates awaiting approval (the only place `delete_approved.py` may delete) |
| `DUPES_SOURCE_ROOT` | The folder the client library was rebuilt from (for the review's "was" paths) |
| `DXV_QUEUE`, `DXV_LOG` | The DXV conversion's queue and log (that step isn't included) |
| `FAILING_DRIVE` | Drive letter of the failing disk; never written, never deleted from |
| `RESCUE_ROOT` | Where its files were rescued to on the NAS |
| `PLEX_LOCAL_URL` | The PC's Plex server (posters) |
| `PLEX_NAS_URL` | The NAS's Plex server (naming check) |
| `PLEX_DB` | The PC's Plex database (read through a backup copy only) |
| `PLEX_TOKEN` | Env only, optional: the Plex token; otherwise read from the local Plex server's Windows settings |
| `KEEP_TITLES`, `TASTE_KEEP` | Your own keep list and taste list for the review's suggestions (empty by default) |

The compose files read their own settings (image versions, folders, logins, bind address) from an env file next to
each one; each file's header lists the keys. The setup script takes `-NasHost` or `NAS_SSH_HOST`.

## Security notes

- The dashboard binds to 127.0.0.1 only. Every request must carry a `localhost`/`127.0.0.1` Host header (against DNS
  rebinding) and no foreign Origin. Every POST must be `Content-Type: application/json` and carry `X-Dashboard: 1`;
  the custom header forces a CORS preflight that the server never grants, so other web pages can't post to it.
  Responses forbid framing and MIME sniffing.
- Actions with side effects are POST only. Opening a file (play, show in Explorer) takes an index or id into a
  server-side list, never a path from the browser, only opens video files, and starts Explorer with an argument
  list rather than a shell string.
- The Plex token is used only server-side, from the environment or Plex's own settings; it is never stored by this
  code or sent to the browser. No secrets are in this repository; rclone and Kopia credentials stay in their own
  config files on each machine.
- Kopia's web UI runs over TLS with a login, with CSRF checks on, and listens on the NAS's loopback by default.
  Exposing it to the LAN is an explicit setting (`KOPIA_BIND_ADDR`); it should never be forwarded to the internet.
- Container images must be pinned to a tested version (`KOPIA_VERSION`, `RCLONE_VERSION`, `PLEX_VERSION`); compose
  refuses to start without them.
- The containers mount media, the backup store (for the offsite copy) and the upload filter read-only. The rclone
  config is writable because rclone refreshes its Google sign-in there.
