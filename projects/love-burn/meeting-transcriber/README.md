Part of Ben Czech's portfolio. Built with Claude Code.

# Meeting transcriber

Turns a multi-track Discord call recording into a speaker-attributed transcript, coloured
subtitles, and a "who's talking" video. Built for the Love Burn camp's weekly planning calls,
which are recorded with [Craig](https://craig.chat/). Craig gives one audio file per participant,
so you know who said what without any diarization model.

## The lesson

**The problem.** Transcribing a mixed call recording gives you text but not who said it. Diarization models
guess speakers from one mixed track, and they guess wrong when people talk over each other.

**The idea.** If the recorder already separates speakers, use that instead of guessing. Transcribe each
person's own track locally, write plain timestamped files, then merge them into one timeline. Every later
output (transcript, subtitles, video) is derived from those files, and each speaker's label and colour
come from one function, so the outputs can't disagree.

### Walkthrough

1. **One track per person.** Craig writes `<n>-<speaker>.<ext>` per participant.
   [`speakers.py`](speakers.py) turns a filename into a label and gives each label a stable colour:
   ```python
   def palette(names: Iterable[str]) -> Dict[str, RGB]:
       """Stable colour per speaker: sorted unique names, assigned round-robin."""
       return {n: BASE_COLOURS[i % len(BASE_COLOURS)] for i, n in enumerate(sorted(set(names)))}
   ```
2. **Transcribe locally, with guard rails.** [`transcribe_fast.py`](transcribe_fast.py) tries the GPU and
   falls back to CPU `int8`. Silence-skipping (VAD) matters here because a single speaker's track is mostly
   silence:
   ```python
   segments, _info = model.transcribe(
       f, language=args.language, vad_filter=True,
       vad_parameters={"min_silence_duration_ms": args.min_silence_ms},
       condition_on_previous_text=False,
   )
   ```
   `condition_on_previous_text=False` keeps one hallucinated line from feeding the next. Stems that
   already have a `.tsv` are skipped unless you pass `--force`, so a rerun only does the missing work.
3. **Use a boring intermediate format.** Each stem gets a `.tsv` (`start`, `end` in ms, `text`). It's easy
   to read, diff and fix by hand, and it keeps the slow step (transcription) apart from the fast ones.
4. **Merge by sorting.** [`merge_transcripts.py`](merge_transcripts.py) reads every TSV in a part, tags each
   row with its speaker, and sorts by start time. That one sorted list produces both the Markdown
   transcript and the colour-coded `.ass` subtitles.
5. **Gate the video on audio, not on timestamps.** [`make_speaker_video.py`](make_speaker_video.py) uses each
   stem's loudness to light up a speaker and show their words, so a segment with a wrong timestamp can't
   leave stale text on screen.

### Try it

You can try the merge step with no audio, no GPU and no model download. It uses only the Python standard
library. Make a folder with two hand-written TSVs:

```
demo/part1/1-speaker_a.tsv          demo/part1/2-speaker_b.tsv
start	end	text                  start	end	text
0	2500	Let's start.          2600	5800	The frame goes up on day two.
6000	8000	Great, thanks.
```

(Columns are separated by tabs, and the first line is a header.) Then run:

```
python merge_transcripts.py demo --part part1 --date 2026-10-04
```

You get `demo/part1-transcript.md` with the two speakers interleaved in time order, and `demo/part1-subs.ass`
with one colour per speaker. To transcribe real stems, install `requirements.txt` and run
`transcribe_fast.py` as shown in **Usage**. Note that its first run downloads the Whisper model.

### Take it to your own project

- Before reaching for a model to recover structure, check whether your source already has it (here, one track per speaker).
- Put a plain, human-editable file between a slow step and the fast steps that follow it.
- Derive shared attributes (names, colours) from one function, so every output agrees.
- Make long batch jobs skip finished work by default, and keep `--force` for redoing it.
- Check the GPU up front and fall back cleanly, so the job fails fast or keeps going instead of failing an hour in.

## Pipeline

| Step | Script | Output |
| :--- | :--- | :--- |
| 1. Transcribe each speaker's stem | `transcribe_fast.py` | `<n>-<speaker>.tsv` next to each stem (`start`, `end` in ms, `text`) |
| 2. Merge all speakers | `merge_transcripts.py` | `<part>-transcript.md` (timestamped Markdown) + `<part>-subs.ass` (speaker-coloured subtitles) |
| 3. Render the video | `make_speaker_video.py` | `<part>-table.mp4`: a table of attendees whose rows light up as they speak, their words in a box beside them, a chapter seek bar, muxed with the mixdown audio |

**Transcription:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper), model `medium`. It runs on
the GPU in `float16` and checks this with a one-second inference up front, so CUDA/cuDNN problems show up
immediately. If that fails it falls back to the CPU with `int8`. Silero VAD (`vad_filter=True`,
`min_silence_duration_ms=700`) skips the long silences in each per-speaker stem, which is most of a
call. `condition_on_previous_text=False` keeps one hallucination from spreading.

**Merging:** segments from every stem are sorted by start time into one timeline. Speaker labels are
the stem filenames, and colours are assigned from a fixed palette in sorted-name order (`speakers.py`),
so the subtitles and the video always match. No names are hard-coded.

**Video:** each stem's audio envelope (ffmpeg decode, RMS per frame, fast attack, ~2 s release) drives
the speaker's dot and gates their text. A transcript segment with a wrong timestamp therefore can't
leave stale words on screen. Long utterances scroll, and a chapter bar (optional
`<part>-chapters.tsv`: `start_ms<TAB>title`) shows progress. Frames are drawn with Pillow and piped
to ffmpeg (`libx264`, 12 fps).

## Layout expected

```
recording/
  part1/            # Craig's per-speaker stems for one recording session
    1-alice.aac
    2-bob.aac
  part1-mixdown.m4a # the mixed track (for the video's audio)
  part1-chapters.tsv  (optional)
```

## Usage

```
pip install -r requirements.txt          # plus ffmpeg on PATH
python transcribe_fast.py recording/part1                     # [--model medium] [--min-silence-ms 700] [--force]
python merge_transcripts.py recording --part part1 --date 2026-10-04 [--title "Camp Meeting"]
python make_speaker_video.py recording --part part1 [--mixdown recording/part1-mixdown.m4a] [--title "..."]
python make_speaker_video.py recording --part part1 --preview 1200    # a single frame at 20:00 as a PNG
```

## Privacy

The scripts carry no recordings, transcripts or participant names. Speaker labels come from whatever
the stem files are called, so rename the stems (`1-speaker_a.aac`) if the output will be shared beyond
the people who were on the call. `.gitignore` keeps audio, transcripts and renders out of git.
