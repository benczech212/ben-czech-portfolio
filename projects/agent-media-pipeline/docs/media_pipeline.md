# Media pipeline: TV / Movies

Drafted 2026-09-29 as the plan, before any of it ran; the format standard and checks below are what the code does.
Paths use the names from `dashboard/config.py`: `MEDIA_ROOT` (the NAS library), `RESCUE_ROOT` (the rescued copy of
a failing drive), `FAILING_DRIVE`.

## Order
1. **Rescue:** copy the Movies/TV files that exist only on the failing drive to `RESCUE_ROOT` on the NAS (separate
   rescue scripts, not included here).
2. **Keep/drop review:** the Plex review tab in the dashboard (`build_plex_review.py` builds it from a backup copy of
   the Plex database). Suggestions are pre-filled; nothing counts until you mark it.
3. **Delete the approved drops,** logged (`delete_plex_drops.py`, dry run first). Extra copies of the same movie go
   too, keeping the best one.
4. **Set up Plex on the NAS** (Docker, `nas/docker/plex/compose.yaml`, hardware transcoding).
5. **Stage, then re-encode, one title at a time:** `stage_media.py` places every kept title at its final name under
   `MEDIA_ROOT` (copied from the PC, or moved when it's already on the NAS). The re-encode worker then fixes the format
   where needed, verifies the result, and replaces the staged copy only after every check passes. Original sources are
   never changed.
6. **Plex points at the organized tree;** the naming check keeps it tidy afterwards.

## Format standard ("ideal file"), decided 2026-09-30
For anything marked **Keep**, movies and TV alike: **at most 1080p and at most 2.5 GB per hour of running time.**

| | Standard | Why |
|---|---|---|
| Container | MKV | holds any codec, subtitles and chapters |
| Video | HEVC (H.265) 10-bit; 4K is scaled to 1080p (HDR information kept); never upscaled | about half the size of H.264 at the same quality; the NAS's N100 can convert it on the fly for older devices |
| 1080p size | fixed average bitrate per title: 2.5 GB/h is ~5.5 Mbps in total, minus the audio (≈4.9 Mbps video with 5.1 audio) | the file can't pass the cap and streams easily |
| 720p / SD | constant quality (lands around 1.3 / 0.7 GB/h) | already well under the cap |
| Audio | the main track plus English ones, at most 2; DTS / TrueHD / FLAC / PCM become E-AC3 (5.1 at 640 kbps, stereo 224 kbps); AC3/E-AC3/AAC kept as-is | the big lossless tracks would eat the budget, and DTS forces conversion on most players |
| Subtitles | all kept; MP4 text subtitles become SRT | small |

**What gets re-encoded:** a kept title is re-encoded when it's 4K, over 2.5 GB/h (plus 5% leeway), or in an old codec
(XviD/DivX/WMV/MPEG-2...). If only the audio or the container is the problem, the video is copied untouched ("audio" or
"remux" job).

**Checks before a job counts as done** (`reencode_worker.py`, `process`): same length as the source (within 2 s or
0.5%), the chosen audio tracks all present, a video stream present, no larger than 1080p, no more than 2.5 GB/h (plus
3%), a full decode with no errors, and for re-encoded video a VMAF score of **at least 70** (`VMAF_MIN`). VMAF is
measured on three 10-second samples at 20%, 50% and 80% of the running time; each sample takes the best of a few
frame alignments (seeks in two files can land a frame or two apart), and the lowest of the three samples is the
title's score. The output is written as `<name>.partial.mkv` and renamed to the final name only after every check
passes; a failed check deletes the partial file and marks the job failed with the reason.

Why 70 and not 90: the bar is there to catch broken encodes. In the encode preview (`build_encode_preview.py`),
grainy film scored about 75-80 at the 2.5 GB/h cap because the encoder smooths grain, and looked fine side by side.
**Observed results:** 251 titles re-encoded, 246 GB → 127 GB; the lowest per-title VMAF was 70.4, the median 94.5.

**Estimated effect on the whole library (before pruning):** movies 1.02 TB → ~0.58 TB, and TV episodes needing work
1.24 TB → ~0.93 TB. Pruning cuts more on top of that.

**Encoder:** the PC's NVIDIA GPU (NVENC, HEVC 10-bit, slowest/highest-quality preset p7, lookahead and adaptive
quantization). It runs many times faster than real time, so the library takes days, not months; CPU x265 was too slow
for 5 TB. The worker only encodes while the PC is idle (with a cancellable countdown) unless set to run anytime, and
pauses the encoder the moment the PC is used.

## Renaming (from Plex's own match)
- Movies: `MEDIA_ROOT\Movies\Title (Year).mkv`, flat with no folder per movie; an edition is added as `Title (Year) - Extended.mkv`.
- TV: `MEDIA_ROOT\TV Shows\Show (Year)\Season 01\Show (Year) - s01e01 - Episode Title.mkv`
- No IMDb/TVDB IDs in folder or file names.
- Subtitle files follow the video: `….en.srt`, `….en.forced.srt`.
- A file is renamed automatically only when Plex has matched it and the file name agrees with that match (year and
  title for a film, season and episode number for an episode). Everything else waits in the Library names tab.
  Every rename is logged in `rename_log.csv` as one batch with its sidecar files, and can be undone from the tab.
- Titles Plex couldn't identify need a "Fix Match" in Plex first, or get renamed by hand.
- Movie packs (a folder of several films) are split into individual movies; Plex collections replace the folder grouping.
- Episodes Plex filed under a movie can be kept as TV ("Keep in TV" in the review): they're filed under the right
  show, season and episode names.

## Watch history
Turn on **Plex → Settings → Account → "Sync my watch state and ratings"**. It stores watched status with the Plex
account, so the new NAS server picks it up for every matched title. Copying the old database across is the fallback.
