"""What Spotify is playing, from its window title ("Artist - Song" while playing).

No Spotify login or extra packages: the desktop app puts the current track in its window title,
and shows just "Spotify ..." when paused. Used to caption recordings.

  python tools/now_playing.py
"""

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _exe(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(512)
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(h)


def spotify_titles():
    titles = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _exe(pid.value).lower().endswith("spotify.exe"):
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                titles.append(buf.value)
        return True

    user32.EnumWindows(each, 0)
    return titles


def now_playing():
    """(title, artist) of the playing track, or None when Spotify is paused / not running."""
    for t in spotify_titles():
        if " - " in t and not t.startswith("Spotify"):
            artist, title = t.split(" - ", 1)       # "Artist - Song - Remix": the song keeps its dash
            return title.strip(), artist.strip()
    return None


if __name__ == "__main__":
    print(now_playing() or "nothing playing", "| windows:", spotify_titles())
