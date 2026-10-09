"""Rolling filters for the sensor inputs, so one bad sample cannot make a decision.

Every threshold on this node is a comparison against a single instantaneous reading:
is the staff still enough to arm, is it turning fast enough to be a sweep, has it
moved enough to wake the GPS. That makes a single spike expensive - it restarts a
two-second hold you had nearly finished, or it starts a capture you never began.

WHY MEDIAN IS THE DEFAULT AND NOT MEAN
--------------------------------------
"Average the last few frames" is the obvious fix and it is the weaker one. A mean
*spreads* a spike instead of removing it: one 8 deg/s blip in a window of three still
lifts the output by 2.7, which is plenty to cross a threshold. A median of three
discards it completely, because an isolated outlier can never be the middle value.
The two have the same lag and the same cost.

So `median` is the default. `mean` is here because it is smoother on signals that are
genuinely noisy rather than spiky - it is the better choice if you ever see the gyro
sitting on a vibration rather than jumping - and `off` is here because a filter you
cannot switch off is a filter you cannot debug.

WHAT IT COSTS
-------------
Half a window of lag, and a slightly clipped peak on a fast gesture. Neither matters
much at three frames, but both are real:

  * The *net* rotation of a sweep survives a symmetric filter almost untouched, which
    is the number `spin_dir` and the cast's size come from.
  * The *peak* rate is what `intensity` is scaled by, and a median trims it a little.
    Widen the window and casts read slightly gentler; that is the filter working, not
    the detector drifting.

The window counts **frames, not time**, and this node deliberately samples faster
while armed (`--cast-hz`) than at rest (`--imu-hz`). Three frames is ~300 ms of
stillness but ~100 ms of gesture. That is the right way round - it is patient when
waiting and quick when reading - but it is worth knowing when a number surprises you.
"""
from __future__ import annotations

from collections import deque
from typing import Iterable, Sequence, Tuple

OFF = "off"
MEAN = "mean"
MEDIAN = "median"
MODES = (OFF, MEAN, MEDIAN)

MAX_WINDOW = 25          # past this the lag is worse than the spikes


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


class Filter:
    """The last `window` samples, reduced to one number.

    Warms up on whatever it has rather than on zeros, so the first reading after a
    restart is the first reading and not a fade-in from nowhere.
    """

    def __init__(self, window: int = 3, mode: str = MEDIAN):
        self._buf: deque = deque(maxlen=1)
        self._mode = MEDIAN
        self.mode = mode
        self.window = window

    # ---- settings, changeable while running ---------------------------------
    @property
    def window(self) -> int:
        return self._buf.maxlen

    @window.setter
    def window(self, n) -> None:
        n = max(1, min(MAX_WINDOW, int(round(float(n)))))
        if n == self._buf.maxlen:
            return
        # Keep what we already know. Rebuilding empty would make every settings change
        # look like a spike, which is the opposite of the point.
        self._buf = deque(list(self._buf)[-n:], maxlen=n)

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value) -> None:
        value = str(value).lower()
        if value in MODES:
            self._mode = value

    @property
    def active(self) -> bool:
        return self._mode != OFF and self.window > 1

    # ---- use ----------------------------------------------------------------
    def push(self, value: float) -> float:
        """Add a sample, get the filtered reading back."""
        value = float(value)
        if not self.active:
            self._buf.clear()
            self._buf.append(value)
            return value
        self._buf.append(value)
        if self._mode == MEAN:
            return sum(self._buf) / len(self._buf)
        return _median(self._buf)

    def reset(self) -> None:
        self._buf.clear()


class VectorFilter:
    """One `Filter` per axis.

    The axes are filtered rather than the magnitude because the turn rate about the
    world vertical is a *projection* of the gyro vector onto gravity - it needs the
    vector, not its length. Filtering once here means the magnitude, the projection
    and anything else downstream all come from the same smoothed sample instead of
    disagreeing about what the staff just did.
    """

    def __init__(self, size: int = 3, window: int = 3, mode: str = MEDIAN):
        self.axes = [Filter(window, mode) for _ in range(size)]

    @property
    def window(self) -> int:
        return self.axes[0].window

    @window.setter
    def window(self, n) -> None:
        for f in self.axes:
            f.window = n

    @property
    def mode(self) -> str:
        return self.axes[0].mode

    @mode.setter
    def mode(self, value) -> None:
        for f in self.axes:
            f.mode = value

    @property
    def active(self) -> bool:
        return self.axes[0].active

    def push(self, vec: Iterable[float]) -> Tuple[float, ...]:
        return tuple(f.push(v) for f, v in zip(self.axes, vec))

    def reset(self) -> None:
        for f in self.axes:
            f.reset()
