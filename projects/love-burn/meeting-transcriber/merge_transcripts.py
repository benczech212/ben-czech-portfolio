"""Merge per-speaker TSVs into (a) a readable Markdown transcript and (b) speaker-coloured
ASS subtitles, one pair per part.

    python merge_transcripts.py RECORDING_DIR --part part1 --date 2026-10-04
    python merge_transcripts.py RECORDING_DIR --part part1 --part part2 --date 2026-10-04 --title "Camp Meeting"

Reads RECORDING_DIR/<part>/<n>-<speaker>.tsv (written by transcribe_fast.py) and writes
RECORDING_DIR/<part>-transcript.md and RECORDING_DIR/<part>-subs.ass. Speaker colours come
from the stem filenames (speakers.py), so they match the video renderer.
"""
import argparse
import csv
import glob
import os

from speakers import palette, speaker_name


def ass_color(rgb):
    r, g, b = rgb            # ASS wants &H00BBGGRR
    return f"&H00{b:02X}{g:02X}{r:02X}"


def hms(ms):
    s = ms // 1000
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def ass_time(ms):
    cs = ms // 10
    return f"{cs // 360000:d}:{cs % 360000 // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,30,&H00FFFFFF,&H00FFFFFF,&H00100B0D,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,40,40,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def merge_part(rec_dir, part, date, title):
    rows = []
    for f in glob.glob(os.path.join(rec_dir, part, "*.tsv")):
        name = speaker_name(f)
        with open(f, encoding="utf-8") as fh:
            rd = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
            next(rd, None)
            for rec in rd:
                if len(rec) < 3:
                    continue
                t = "\t".join(rec[2:]).strip()
                if t:
                    rows.append((int(rec[0]), int(rec[1]), name, t))
    rows.sort()
    colours = palette(r[2] for r in rows)

    md = os.path.join(rec_dir, f"{part}-transcript.md")
    with open(md, "w", encoding="utf-8") as out:
        out.write(f"# {title} {date} — {part} transcript\n\n")
        out.write("Auto-transcribed (Whisper); speaker per Craig track. Times from recording start.\n\n")
        for start, _end, name, text in rows:
            out.write(f"**[{hms(start)}] {name}:** {text}\n\n")

    ass = os.path.join(rec_dir, f"{part}-subs.ass")
    with open(ass, "w", encoding="utf-8") as out:
        out.write(ASS_HEADER)
        for start, end, name, text in rows:
            c = ass_color(colours.get(name, (255, 255, 255)))
            safe = text.replace("\\", "/").replace("{", "(").replace("}", ")")
            out.write(f"Dialogue: 0,{ass_time(start)},{ass_time(max(end, start + 800))},Default,,0,0,0,,{{\\c{c}}}{safe}\n")
    print(part, len(rows), "segments merged ->", md, "+", ass)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording_dir", help="folder holding the part folders (part1/, part2/, ...)")
    ap.add_argument("--part", action="append", required=True, help="part folder name; repeat for several")
    ap.add_argument("--date", required=True, help="meeting date for the transcript heading, e.g. 2026-10-04")
    ap.add_argument("--title", default="Camp Meeting", help='heading prefix (default: "Camp Meeting")')
    args = ap.parse_args()
    for part in args.part:
        merge_part(args.recording_dir, part, args.date, args.title)


if __name__ == "__main__":
    main()
