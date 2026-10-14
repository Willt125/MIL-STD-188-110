"""Slowly changing channel parameters: schedules of a value (in dB) against time.

Appendix E's Watterson model is stationary: its SNR and path powers are fixed for the length
of a test. Real HF links drift (absorption, the MUF, a path coming and going), over minutes
rather than the fraction of a second the fading works on. A :class:`Schedule` describes such
a drift so that :class:`~m110c.channel.watterson.TimeVaryingChannel` can follow it.

A schedule passes through a list of ``(time_s, value)`` points:

* with ``volatility`` 0, it is a straight line between successive points (A -> B, or
  A -> B -> C ...), and constant before the first point and after the last;
* with ``volatility`` > 0, each stretch between two points is a Brownian bridge: a random walk
  that starts at one point and is pinned to arrive exactly at the next. ``volatility`` is the
  walk's standard deviation per square root of a second (dB/sqrt(s)); midway along a stretch
  of T seconds the walk strays from the straight line by about volatility * sqrt(T) / 2.

The walk is drawn on a grid (``grid_hz``, default 4 Hz: far slower than any fading, far
faster than any drift worth modelling) and linearly interpolated, so :meth:`Schedule.value`
is a pure function of time: reproducible from the seed and independent of how a channel's
input is chunked. ``bounds`` clips the result to a physical range.

This is our extension of the model, not part of Appendix E or ITU-R F.1487, which describe
stationary channels; a sequence of quasi-stationary channels with smooth transitions is the
usual reading of how such models apply over longer times.
"""

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Schedule:
    points: tuple  # ((time_s, value), ...), times strictly increasing
    volatility: float = 0.0  # random-walk standard deviation, per sqrt(second)
    seed: int = 0
    grid_hz: float = 4.0
    bounds: tuple | None = None  # (low, high): clip the value
    _grid: tuple = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        pts = tuple((float(t), float(v)) for t, v in self.points)
        if not pts:
            raise ValueError("a schedule needs at least one point")
        times = [t for t, _ in pts]
        if any(b <= a for a, b in zip(times, times[1:], strict=False)):
            raise ValueError("schedule times must be strictly increasing")
        if self.volatility < 0 or self.grid_hz <= 0:
            raise ValueError("volatility must be >= 0 and grid_hz > 0")
        object.__setattr__(self, "points", pts)
        object.__setattr__(self, "_grid", self._build_grid())

    def _build_grid(self):
        """Knots (times, values) that :meth:`value` interpolates between."""
        if self.volatility == 0 or len(self.points) == 1:
            return tuple(np.array(c) for c in zip(*self.points, strict=True))
        rng = np.random.default_rng(self.seed)
        times, values = [self.points[0][0]], [self.points[0][1]]
        for (t0, v0), (t1, v1) in zip(self.points, self.points[1:], strict=False):
            n = max(1, int(np.ceil((t1 - t0) * self.grid_hz)))
            t = np.linspace(t0, t1, n + 1)
            steps = rng.standard_normal(n) * self.volatility * np.sqrt(np.diff(t))
            walk = np.concatenate([[0.0], np.cumsum(steps)])
            bridge = walk - (t - t0) / (t1 - t0) * walk[-1]  # pinned to 0 at both ends
            times.extend(t[1:])
            values.extend((v0 + (t - t0) / (t1 - t0) * (v1 - v0) + bridge)[1:])
        return np.array(times), np.array(values)

    def value(self, t_s):
        """The scheduled value at time(s) ``t_s`` (seconds; scalar or array)."""
        times, values = self._grid
        out = np.interp(t_s, times, values)
        if self.bounds is not None:
            out = np.clip(out, *self.bounds)
        return out


def constant(value: float) -> Schedule:
    return Schedule(((0.0, value),))


def linear(a: float, b: float, duration_s: float, start_s: float = 0.0) -> Schedule:
    """``a`` until ``start_s``, then a straight line to ``b`` over ``duration_s``, then ``b``."""
    return Schedule(((start_s, a), (start_s + duration_s, b)))


def walk(
    a: float,
    b: float,
    duration_s: float,
    volatility: float,
    seed: int = 0,
    start_s: float = 0.0,
    bounds: tuple | None = None,
) -> Schedule:
    """A random walk from ``a`` that arrives at ``b`` after ``duration_s`` (Brownian bridge)."""
    return Schedule(
        ((start_s, a), (start_s + duration_s, b)), volatility=volatility, seed=seed, bounds=bounds
    )


def as_schedule(x) -> Schedule | None:
    """None stays None; a number becomes a constant schedule."""
    if x is None or isinstance(x, Schedule):
        return x
    return constant(float(x))
