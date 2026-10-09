"""Shared by the dashboard server and the re-encode worker: the queue file, a cross-process lock, and the
format standard. Nothing here touches media files. Windows only (msvcrt file locks)."""
import contextlib, hashlib, json, msvcrt, os, time
import config

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "reencode_queue.json")
LOCKFILE = os.path.join(HERE, "reencode_queue.lock")
FFMPEG, FFPROBE = config.FFMPEG, config.FFPROBE   # an ffmpeg build with hevc_nvenc, libvmaf and zscale

# Format standard (docs/media_pipeline.md): at most 1080p and at most 2.5 GB per hour of running time.
# 1080p (and 4K, which is scaled to 1080p) uses a fixed average bitrate worked out per title so the file can't pass
# the cap; SD and 720p use constant quality, which lands well under it.
CAP_GBH = 2.5
VIDEO_CQ = {"SD": (25, "3M"), "720p": (25, "6M")}  # tier -> (cq, maxrate)
BAD_AUDIO = ("dts", "dca", "truehd", "mlp", "wmav2", "wmapro", "mp2", "flac", "pcm_s16le", "pcm_s24le", "pcm_bluray")
MAX_AUDIO_TRACKS = 2  # the main track plus English ones
ACTIVE = ("queued", "running", "verifying")


@contextlib.contextmanager
def queue_lock(timeout=30):
    """Exclusive lock shared by every process that edits the queue file."""
    fd = os.open(LOCKFILE, os.O_RDWR | os.O_CREAT)
    end = time.time() + timeout
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if time.time() > end:
                os.close(fd)
                raise TimeoutError("queue is locked")
            time.sleep(0.1)
    try:
        yield
    finally:
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def load():
    # the file is replaced atomically, but Windows can briefly refuse access while another process swaps it in
    for _ in range(20):
        try:
            with open(QUEUE, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {"jobs": [], "worker": {}}
        except (PermissionError, ValueError):
            time.sleep(0.1)
    return {"jobs": [], "worker": {}}


def save(q):
    tmp = QUEUE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, indent=1)
    for i in range(50):  # a reader may hold the file open for a moment; Windows then refuses the replace
        try:
            os.replace(tmp, QUEUE)
            return
        except PermissionError:
            time.sleep(0.1)
    raise PermissionError(f"couldn't update {QUEUE} (kept open by another program)")


def job_id(dest):
    return hashlib.sha1(dest.lower().encode()).hexdigest()[:12]


def res_tier(width):
    return "4K" if width > 2000 else "1080p" if width > 1300 else "720p" if width > 900 else "SD"


def staged_names(dest, srcs):
    """Where the copy step puts a title before re-encoding: the final name with the source's own file type;
    a copy split into parts gets ' - pt1', ' - pt2' (Plex's multi-part naming)."""
    base = dest[:-4] if dest.lower().endswith(".mkv") else dest
    if len(srcs) == 1:
        return [base + os.path.splitext(srcs[0])[1].lower()]
    return [f"{base} - pt{i + 1}{os.path.splitext(x)[1].lower()}" for i, x in enumerate(srcs)]
