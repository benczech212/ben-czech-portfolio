#!/usr/bin/env bash
# Render a sound as two stereo sweeps, for the staff's direction-following cast sound.
#
#   tools/make_panned.sh <in.wav> [outdir]
#     -> <outdir>/<name>_L2R.wav   left to right over the sound's length
#        <outdir>/<name>_R2L.wav   right to left
#
# Runs on a PC with ffmpeg, NOT on the Pi Zero: panning is rendered once here rather
# than computed live on one ARMv6 core. Copy the results to the Pi, e.g.
#
#   scp out/*_L2R.wav out/*_R2L.wav <user>@<pi-ip>:sounds/magic/panned/
#
# and map them per direction in the unit:
#   --sound-map '*=magic/08-teleport.wav,spin_cw=magic/panned/08-teleport_L2R.wav,spin_ccw=magic/panned/08-teleport_R2L.wav'
#
# The pan is equal-power: the input is folded to mono, then
#   left  = m * cos(theta),  right = m * sin(theta),  theta = 0 -> pi/2 over the sound
# so loudness stays constant across the sweep (a linear crossfade dips ~3 dB in the
# middle, which reads as the sound moving away and coming back). Output is 48 kHz
# 16-bit stereo, the same as every other file in ~/sounds, so aplay needs no resampling.
#
# The sound files themselves are licensed, not ours to redistribute: they never go in
# git. This script is the reproducible part.
set -euo pipefail

in="${1:?usage: make_panned.sh <in.wav> [outdir]}"
out="${2:-$(dirname "$in")}"
name="$(basename "${in%.*}")"
mkdir -p "$out"

dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$in")"
echo "$name: ${dur}s"

loudness() {   # integrated LUFS of a file
  ffmpeg -nostdin -hide_banner -i "$1" -af ebur128 -f null - 2>&1     | awk '/^ +I:/ {v=$2} END {print v}'
}
peak() {       # sample peak, dBFS
  ffmpeg -nostdin -hide_banner -i "$1" -af astats=measure_overall=Peak_level:measure_perchannel=none     -f null - 2>&1 | awk '/Peak level dB/ {v=$NF} END {print v}'
}

pan() {   # $1 = theta expression in terms of p (0 at the start, 1 at the end), $2 = out, $3 = gain dB
  ffmpeg -nostdin -hide_banner -loglevel error -y -i "$in" -filter_complex "
    [0:a]aformat=sample_rates=48000:channel_layouts=stereo,
         pan=mono|c0=0.5*c0+0.5*c1,
         aeval='val(0)*cos($1)|val(0)*sin($1)':channel_layout=stereo,
         volume=${3:-0}dB,
         aformat=sample_fmts=s16"     -ar 48000 -ac 2 -c:a pcm_s16le "$2"
}

# p runs 0 -> 1 over the sound; min() holds the end for any trailing samples.
P="min(1\,t/$dur)"

# Folding to mono drops the level (by ~3 dB when the channels differ), so measure a
# first render and make it up: the panned files then sit at the input's loudness,
# -16 LUFS like everything else in ~/sounds. Never past -1 dBFS peak, though - a
# louder cast that clips is worse than one that is a dB quiet.
tmp="$out/.${name}_probe.wav"
pan "PI/2*$P" "$tmp" 0
want="$(loudness "$in")"; got="$(loudness "$tmp")"; pk="$(peak "$tmp")"
gain="$(awk -v w="$want" -v g="$got" -v p="$pk" 'BEGIN {
  d = w - g; room = -1 - p; if (d > room) d = room; printf "%.2f", d }')"
rm -f "$tmp"
echo "  input $want LUFS, panned $got LUFS, peak $pk dBFS -> gain ${gain} dB"

pan "PI/2*$P" "$out/${name}_L2R.wav" "$gain"          # theta 0 (all left) -> pi/2 (all right)
pan "PI/2*(1-$P)" "$out/${name}_R2L.wav" "$gain"      # and back
for f in "$out/${name}_L2R.wav" "$out/${name}_R2L.wav"; do
  echo "  -> $f  ($(loudness "$f") LUFS, peak $(peak "$f") dBFS)"
done
