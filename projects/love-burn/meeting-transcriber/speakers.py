"""Speaker names and colours, derived from Craig stem filenames.

Craig (the Discord recording bot) writes one audio file per participant, named
``<track-number>-<username>.<ext>`` (e.g. ``3-someone.aac``). The speaker label is the part
after the first dash. Colours come from a fixed palette in sorted-name order, so the
transcript, the subtitles and the video all agree without a hard-coded name list.
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, Tuple

RGB = Tuple[int, int, int]

# Distinct on a dark background; cycles if there are more speakers than colours.
BASE_COLOURS: Tuple[RGB, ...] = (
    (79, 216, 224),    # cyan
    (255, 79, 163),    # pink
    (255, 184, 79),    # amber
    (123, 224, 122),   # green
    (180, 127, 255),   # purple
    (255, 107, 94),    # coral
    (90, 140, 255),    # blue
    (245, 245, 245),   # white
    (224, 224, 92),    # yellow
    (92, 224, 176),    # mint
)


def speaker_name(path: str) -> str:
    """'part1/3-someone.aac' -> 'someone' (the whole stem if there is no dash)."""
    stem = os.path.basename(path).rsplit(".", 1)[0]
    return stem.split("-", 1)[1] if "-" in stem else stem


def palette(names: Iterable[str]) -> Dict[str, RGB]:
    """Stable colour per speaker: sorted unique names, assigned round-robin."""
    return {n: BASE_COLOURS[i % len(BASE_COLOURS)] for i, n in enumerate(sorted(set(names)))}
