"""Tests for channel schedules and the time-varying Watterson channel (our extension of
Appendix E: see m110c/channel/schedule.py)."""

import itertools

import numpy as np
import pytest

from m110c.channel import schedule as S
from m110c.channel.watterson import (
    DEFAULT_SIGNAL_POWER,
    ChannelSpec,
    TimeVaryingChannel,
    WattersonChannel,
)

FS = 9600


def noise_input(n, seed=0):
    rng = np.random.default_rng(seed)
    return 0.4 * (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)


# --- schedules -----------------------------------------------------------------------------


def test_linear_holds_then_ramps_then_holds():
    s = S.linear(17.0, 9.0, duration_s=40.0, start_s=10.0)
    assert s.value(0.0) == 17.0 and s.value(10.0) == 17.0
    assert s.value(30.0) == pytest.approx(13.0)  # halfway
    assert s.value(50.0) == 9.0 and s.value(1e6) == 9.0


def test_piecewise_points_are_joined_by_straight_lines():
    s = S.Schedule(((0, 10.0), (10, 20.0), (30, 0.0)))
    assert s.value(np.array([5.0, 10.0, 20.0, 30.0])).tolist() == pytest.approx([15, 20, 10, 0])


def test_a_walk_passes_through_every_point_and_wanders_between():
    s = S.Schedule(((0, 15.0), (60, 11.0), (120, 14.0)), volatility=1.0, seed=3)
    assert s.value(np.array([0.0, 60.0, 120.0])).tolist() == pytest.approx([15.0, 11.0, 14.0])
    t = np.linspace(0, 120, 481)
    line = S.Schedule(((0, 15.0), (60, 11.0), (120, 14.0))).value(t)
    assert np.max(np.abs(s.value(t) - line)) > 1.0  # it does wander


def test_a_walk_is_reproducible_from_its_seed():
    a, b, c = (S.walk(15, 11, 100, 0.8, seed=k) for k in (1, 1, 2))
    t = np.linspace(0, 100, 401)
    assert np.array_equal(a.value(t), b.value(t)) and not np.array_equal(a.value(t), c.value(t))


def test_walk_spread_matches_its_volatility():
    """Midway along a bridge of T seconds the spread is volatility * sqrt(T) / 2."""
    vol, duration = 0.5, 100.0
    mid = np.array([S.walk(0, 0, duration, vol, seed=k).value(duration / 2) for k in range(400)])
    expected = vol * np.sqrt(duration) / 2
    assert np.std(mid) == pytest.approx(expected, rel=0.15)
    assert abs(np.mean(mid)) < 3 * expected / np.sqrt(400)


def test_bounds_clip_a_walk():
    s = S.walk(0, 0, 200, 3.0, seed=4, bounds=(-2.0, 2.0))
    v = s.value(np.linspace(0, 200, 2001))
    assert v.min() >= -2.0 and v.max() <= 2.0
    assert (v == 2.0).any() or (v == -2.0).any()  # the walk did reach a bound


@pytest.mark.parametrize("points", [(), ((1, 0), (1, 1)), ((2, 0), (1, 1))])
def test_bad_schedules_are_rejected(points):
    with pytest.raises(ValueError):
        S.Schedule(points)


# --- the time-varying channel ---------------------------------------------------------------


@pytest.mark.parametrize(
    "spec", [ChannelSpec(2, 2.0, 1.0), ChannelSpec(2, 5.0, 0.0), ChannelSpec(1, 0.0, 0.0)]
)
def test_constant_schedules_reproduce_the_appendix_e_channel_exactly(spec):
    """Same streams, same arithmetic: the validated channel, sample for sample."""
    x = noise_input(40_000)
    reference = WattersonChannel(
        ChannelSpec(spec.paths, spec.delay_ms, spec.doppler_hz, 12.0), seed=5
    ).process(x)
    plain = TimeVaryingChannel(spec, snr_db=12.0, seed=5).process(x)
    scheduled = TimeVaryingChannel(
        spec, snr_db=S.constant(12.0), path_db=(S.constant(0.0),) * spec.paths, seed=5
    ).process(x)
    assert np.array_equal(plain, reference) and np.array_equal(scheduled, reference)


def test_output_does_not_depend_on_chunking():
    spec = ChannelSpec(2, 2.0, 1.0)
    kwargs = dict(snr_db=S.walk(15, 9, 4.0, 2.0, seed=1), path_db=(0.0, S.linear(0, -6, 3.0)))
    x = noise_input(40_000)
    whole = TimeVaryingChannel(spec, seed=2, **kwargs).process(x)
    ch, out, i = TimeVaryingChannel(spec, seed=2, **kwargs), [], 0
    for size in itertools.cycle([1, 777, 4096, 13]):
        if i >= x.size:
            break
        out.append(ch.process(x[i : i + size]))
        i += size
    assert np.array_equal(np.concatenate(out), whole)


def test_noise_follows_the_snr_schedule():
    """Zero input: the output is the noise alone, whose power tracks the schedule."""
    ch = TimeVaryingChannel(ChannelSpec(1, 0.0, 0.0), snr_db=S.linear(20.0, 0.0, 10.0), seed=3)
    y = ch.process(np.zeros(10 * FS, complex))
    for second in (0, 5, 9):
        window = y[second * FS : (second + 1) * FS]
        snr_mid = 20.0 - 2.0 * (second + 0.5)
        # The average noise power over a second where the SNR falls linearly in dB.
        t = second + np.arange(FS) / FS
        expected = np.mean(DEFAULT_SIGNAL_POWER / 10 ** ((20.0 - 2.0 * t) / 10)) * FS / 3000
        assert np.mean(np.abs(window) ** 2) == pytest.approx(expected, rel=0.05), snr_mid


def test_path_power_schedule_scales_each_path():
    """Fixed (non-fading) paths and single impulses: each echo's size follows its path."""
    L = int(round(5e-3 * FS))  # 5 ms
    ch = TimeVaryingChannel(
        ChannelSpec(2, 5.0, 0.0), path_db=(S.constant(0.0), S.linear(0.0, -20.0, 10.0)), seed=0
    )
    x = np.zeros(10 * FS, complex)
    starts = np.arange(0, 9 * FS, FS)
    x[starts] = 1.0
    y = ch.process(x)
    t_echo = (starts + L) / FS
    assert np.allclose(np.abs(y[starts]), np.sqrt(0.5))  # path 1 unchanged
    expected = np.sqrt(0.5) * 10 ** (-2.0 * t_echo / 20)  # path 2 falls 2 dB per second
    assert np.allclose(np.abs(y[starts + L]), expected)


def test_fading_continues_through_silence():
    """One channel for a whole session: the fading gain after a pause continues the same
    process rather than starting a new, independent one."""
    spec = ChannelSpec(1, 0.0, 1.0)
    ones = np.ones(2 * FS, complex)
    session = TimeVaryingChannel(spec, seed=9)
    first = session.process(ones)
    session.process(np.zeros(FS // 10, complex))  # 0.1 s pause
    after = session.process(ones)
    # 1-Hz fading barely moves in 0.1 s: the gain resumes close to where it stopped.
    assert abs(after[0] - first[-1]) < 0.25 * np.sqrt(np.mean(np.abs(first) ** 2))


def test_snr_must_not_also_be_in_the_spec():
    with pytest.raises(ValueError):
        TimeVaryingChannel(ChannelSpec(2, 2.0, 1.0, 10.0), snr_db=10.0)
    with pytest.raises(ValueError):
        TimeVaryingChannel(ChannelSpec(2, 2.0, 1.0), path_db=(0.0,))


@pytest.mark.parametrize("path", [0, 1])
def test_path_power_schedule_scales_a_fading_path(path):
    """Same seed, same fading: setting one path 20 dB down shrinks exactly its echoes by 10."""
    L = int(round(2e-3 * FS))
    x = np.zeros(4 * FS, complex)
    x[:: FS // 10] = 1.0  # isolated impulses: direct at k, echo at k + L
    spec = ChannelSpec(2, 2.0, 1.0)
    db = [0.0, 0.0]
    nominal = TimeVaryingChannel(spec, path_db=tuple(db), seed=4).process(x)
    db[path] = -20.0
    quieter = TimeVaryingChannel(spec, path_db=tuple(db), seed=4).process(x)
    at = np.flatnonzero(x) + (L if path == 1 else 0)
    other = np.flatnonzero(x) + (0 if path == 1 else L)
    assert np.allclose(np.abs(quieter[at]), 0.1 * np.abs(nominal[at]))
    assert np.allclose(quieter[other], nominal[other])
