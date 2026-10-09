"""Control the Spotify desktop app: play, pause, next, previous.

Sends the media commands (WM_APPCOMMAND) straight to Spotify's window - the same thing the
keyboard's media keys do, but aimed at Spotify only and with no Spotify login or developer app.
(The Spotify Web API could pick exact tracks, but needs an app registration and an OAuth login.)

  python tools/spotify_control.py next|previous|play|pause|playpause|status
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

from now_playing import _exe, now_playing

user32 = ctypes.windll.user32
WM_APPCOMMAND = 0x0319
COMMANDS = {"playpause": 14, "next": 11, "previous": 12, "play": 46, "pause": 47, "stop": 13}


def spotify_window():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(hwnd, _):
        if user32.GetWindowTextLengthW(hwnd) and user32.IsWindowVisible(hwnd):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _exe(pid.value).lower().endswith("spotify.exe"):
                found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else None


def send(command):
    hwnd = spotify_window()
    if not hwnd:
        raise RuntimeError("Spotify isn't running")
    user32.SendMessageW(hwnd, WM_APPCOMMAND, hwnd, COMMANDS[command] << 16)


def start_fresh_song(wait=1.5):
    """Skip to the next song so it plays from its beginning; make sure it's playing."""
    send("next")
    time.sleep(wait)
    if now_playing() is None:          # was paused: next keeps it paused
        send("play")
        time.sleep(wait)
    return now_playing()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd != "status":
        send(cmd)
        time.sleep(1.5)
    print(now_playing() or "paused / nothing playing")
