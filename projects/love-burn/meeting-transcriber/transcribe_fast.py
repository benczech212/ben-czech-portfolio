"""Transcribe Craig per-speaker stems with faster-whisper + Silero VAD (skips silence).

    python transcribe_fast.py RECORDING_DIR/part1 [RECORDING_DIR/part2 ...]
    python transcribe_fast.py recording/part1 --model medium --min-silence-ms 700 --force

Writes a whisper-CLI-compatible .tsv (start\tend\ttext, times in ms) next to each stem.
Stems that already have a .tsv are skipped unless --force. Uses the GPU (float16) when the
CUDA libraries resolve (torch's bundled DLLs are added to the search path on Windows);
otherwise falls back to CPU int8.
"""
import argparse
import glob
import os
import time

# Let ctranslate2 find the cuBLAS/cuDNN DLLs bundled inside torch (Windows; harmless elsewhere).
try:
    import torch
    torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
    if hasattr(os, "add_dll_directory") and os.path.isdir(torch_lib):
        os.add_dll_directory(torch_lib)
    os.environ["PATH"] = torch_lib + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

from faster_whisper import WhisperModel

AUDIO_EXTS = ("aac", "flac", "ogg", "m4a", "wav", "mp3")


def load_model(name: str):
    try:
        import numpy as np
        m = WhisperModel(name, device="cuda", compute_type="float16")
        # a real (tiny) inference surfaces CUDA/cuDNN failures now rather than mid-run
        segs, _ = m.transcribe(np.zeros(16000, dtype=np.float32), language="en")
        list(segs)
        print("using GPU (float16)", flush=True)
        return m
    except Exception as e:
        print("GPU unavailable (%r); using CPU int8" % e, flush=True)
        return WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=os.cpu_count())


def stems(dirs):
    out = []
    for d in dirs:
        for ext in AUDIO_EXTS:
            out += glob.glob(os.path.join(d, "*." + ext))
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", help="folders of per-speaker stems (e.g. recording/part1)")
    ap.add_argument("--model", default="medium", help="faster-whisper model (default: medium)")
    ap.add_argument("--language", default="en")
    ap.add_argument("--min-silence-ms", type=int, default=700, help="VAD: silence that splits segments")
    ap.add_argument("--force", action="store_true", help="re-transcribe stems that already have a .tsv")
    args = ap.parse_args()

    files = stems(args.dirs)
    if not files:
        ap.error("no audio stems found in " + ", ".join(args.dirs))
    model = load_model(args.model)
    for f in files:
        tsv = f.rsplit(".", 1)[0] + ".tsv"
        if os.path.exists(tsv) and not args.force:
            print("skip (done):", f, flush=True)
            continue
        t0 = time.time()
        print("transcribing:", f, flush=True)
        segments, _info = model.transcribe(
            f, language=args.language, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": args.min_silence_ms},
            condition_on_previous_text=False,
        )
        n = 0
        with open(tsv, "w", encoding="utf-8") as out:
            out.write("start\tend\ttext\n")
            for seg in segments:
                text = seg.text.strip().replace("\t", " ").replace("\n", " ")
                out.write(f"{int(seg.start * 1000)}\t{int(seg.end * 1000)}\t{text}\n")
                n += 1
        print(f"  {n} segments in {time.time() - t0:.0f}s", flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
