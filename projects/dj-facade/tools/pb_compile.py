"""Compile patterns without downloading the Pixelblaze's web UI every time.

pixelblaze-client's compilePattern() fetches /index.html.gz (the whole web UI, which contains the
compiler) from the Pixelblaze on every call; its own cache is broken. Hammering that while also
streaming stats has knocked the Pixelblaze's network stack over. This keeps one copy on disk per
firmware version and serves it to compilePattern().
"""

import pathlib

CACHE = pathlib.Path(__file__).resolve().parent.parent / ".cache"


def install(pb):
    """Patch this Pixelblaze client so /index.html.gz comes from the local cache after the first time."""
    version = str(pb.getConfigSettings().get("ver", "unknown"))
    path = CACHE / f"pixelblaze-ui-{version}.html.gz"
    original = pb.getFile

    def get_file(name):
        if name == "/index.html.gz":
            if not path.exists():
                CACHE.mkdir(exist_ok=True)
                path.write_bytes(original(name))
            return path.read_bytes()
        return original(name)

    pb.getFile = get_file
    return pb
