"""Paths and addresses for this machine, in one place. Every other script imports its locations from here.

Each setting is read from, in order:
  1. an environment variable of the same name (lists: separated by ';'),
  2. dashboard/config.json (or the file named by MEDIA_PIPELINE_CONFIG); see config.example.json,
  3. the placeholder default below.

The defaults are placeholders, not working values: N:\\ stands for the NAS data share mapped as a drive letter,
\\\\nas\\docker for the NAS's docker share, nas.local for the NAS's host name.

Nothing secret belongs in config.json. The Plex token is read at run time (PLEX_TOKEN, else the local Plex
server's own Windows settings) and is never written anywhere or sent to the browser.
"""
import json, os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
_FILE = os.environ.get("MEDIA_PIPELINE_CONFIG") or os.path.join(HERE, "config.json")
try:
    with open(_FILE, encoding="utf-8") as _f:
        _CFG = json.load(_f)
except FileNotFoundError:
    _CFG = {}


def get(key, default):
    v = os.environ.get(key)
    if v is not None and v != "":
        if isinstance(default, list):
            return [x for x in v.split(";") if x]
        if isinstance(default, int):
            return int(v)
        return v
    return _CFG.get(key, default)


# ---- dashboard ----
PORT = get("DASHBOARD_PORT", 8765)
WORK_DIR = get("WORK_DIR", os.path.join(HERE, "work"))      # scratch: Plex DB copy, preview clips, inputs from other steps

# ---- tools (blank = found on PATH) ----
_ffdir = get("FFMPEG_DIR", "")    # needs an ffmpeg build with hevc_nvenc, libvmaf and zscale
FFMPEG = os.path.join(_ffdir, "ffmpeg.exe") if _ffdir else (shutil.which("ffmpeg") or "ffmpeg")
FFPROBE = os.path.join(_ffdir, "ffprobe.exe") if _ffdir else (shutil.which("ffprobe") or "ffprobe")
RCLONE = get("RCLONE", "") or shutil.which("rclone") or "rclone"

# ---- NAS ----
NAS_DATA = get("NAS_DATA", "N:\\")                                   # the NAS data share as seen from the PC
MEDIA_ROOT = get("MEDIA_ROOT", os.path.join(NAS_DATA, "Media"))       # holds Movies\ and TV Shows\ (what the NAS Plex serves)
NAS_RECYCLE = get("NAS_RECYCLE", os.path.join(NAS_DATA, "#recycle"))
NAS_DOCKER_SHARE = get("NAS_DOCKER_SHARE", r"\\nas\docker")          # the compose projects' folders (nas/docker/*)

# ---- the client media library mirrored to Google Drive (nas/docker/rclone-uploader) ----
CLIENT_LIBRARY = get("CLIENT_LIBRARY", os.path.join(NAS_DATA, "Client Media Library"))
CLIENT_LIBRARY_REMOTE = get("CLIENT_LIBRARY_REMOTE", "gdrive:Client Media Library")
UPLOAD_FILTER = get("UPLOAD_FILTER", os.path.join(NAS_DOCKER_SHARE, "rclone-uploader", "filter.txt"))
UPLOAD_LOG = get("UPLOAD_LOG", os.path.join(NAS_DOCKER_SHARE, "rclone-uploader", "upload.log"))
OFFSITE_LOG = get("OFFSITE_LOG", os.path.join(NAS_DOCKER_SHARE, "kopia", "offsite-logs", "offsite.log"))
DUPES_DIR = get("DUPES_DIR", os.path.join(NAS_DATA, "_duplicates to review"))   # parked duplicates awaiting approval
DUPES_SOURCE_ROOT = get("DUPES_SOURCE_ROOT", os.path.join(NAS_DATA, "Library Backup"))  # where the library was rebuilt from
DXV_QUEUE = get("DXV_QUEUE", os.path.join(WORK_DIR, "dxv_queue.csv"))   # written by the DXV conversion (not included)
DXV_LOG = get("DXV_LOG", os.path.join(WORK_DIR, "convert.log"))

# ---- the failing drive and its rescued copy on the NAS ----
FAILING_DRIVE = get("FAILING_DRIVE", "E:")                            # never read for deletes, never written
RESCUE_ROOT = get("RESCUE_ROOT", os.path.join(NAS_DATA, "Rescue"))    # FAILING_DRIVE\x\y is at RESCUE_ROOT\x\y

# ---- Plex ----
PLEX_LOCAL_URL = get("PLEX_LOCAL_URL", "http://127.0.0.1:32400")      # the PC's Plex (old library, posters)
PLEX_NAS_URL = get("PLEX_NAS_URL", "http://nas.local:32400")          # the NAS's Plex (the organized library)
PLEX_DB = get("PLEX_DB", os.path.expandvars(
    r"%LOCALAPPDATA%\Plex Media Server\Plug-in Support\Databases\com.plexapp.plugins.library.db"))

# ---- review suggestions (build_plex_review.py): your own lists, empty by default ----
KEEP_TITLES = get("KEEP_TITLES", [])     # title substrings to always suggest Keep, e.g. ["some franchise"]
TASTE_KEEP = _CFG.get("TASTE_KEEP", {})  # {"title substring": "reason shown in the review"}


def plex_token():
    """The Plex access token: PLEX_TOKEN, else the local Plex server's Windows settings. Used server-side only;
    never logged, stored or sent to the browser."""
    tok = os.environ.get("PLEX_TOKEN")
    if tok:
        return tok
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Plex, Inc.\Plex Media Server") as k:
            return winreg.QueryValueEx(k, "PlexOnlineToken")[0]
    except (OSError, ImportError):
        return None
