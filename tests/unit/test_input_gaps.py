"""Gaps in the input: samples a sound card dropped, given to the receiver as zeros (the
station's InputRing keeps the time base). A message survives a gap, losing at most what the
gap covered (docs/PERFORMANCE.md, P1)."""

import numpy as np
import pytest

from m110c.modulate import modulate
from m110c.rx.receiver import Receiver
from m110c.spec import mode
from m110c.tx import transmit_bytes


def received_through_a_gap(rate, interleave, at_s, gap_s, seed=0):
    data = bytes(np.random.default_rng(seed).integers(0, 256, rate, dtype=np.uint8))
    x = modulate(transmit_bytes(data, mode(rate, interleave)))
    x = np.concatenate([np.zeros(4800, complex), x, np.zeros(9600 * 3, complex)])
    rng = np.random.default_rng(seed + 1)
    x = x + 0.02 * (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))
    a = int(at_s * 9600)
    x[a : a + int(gap_s * 9600)] = 0
    rx = Receiver()
    return data, rx.process(x) + rx.finish(), rx.messages


@pytest.mark.parametrize("at_s", [3.37, 4.51, 6.05])
def test_2400_bps_keeps_its_phase_across_a_gap(at_s):
    """After a 20-50 ms gap, 2400S decisions came out turned by 180 degrees with the channel
    estimate and stayed so for seconds, outvoting the probes (every byte after the gap
    inverted until the frequency error drifted it back). The probe now turns it back."""
    data, out, messages = received_through_a_gap(2400, "short", at_s, 0.05)
    assert out == data
    assert len(messages) == 1 and messages[0].eom_found


@pytest.mark.parametrize(("rate", "interleave"), [(1200, "short"), (75, "short"), (600, "long")])
def test_other_rates_survive_a_tenth_of_a_second(rate, interleave):
    data, out, _ = received_through_a_gap(rate, interleave, 4.0, 0.1)
    assert out == data


def test_the_correction_is_noted():
    *_, messages = received_through_a_gap(2400, "short", 4.51, 0.02)
    assert any(note.startswith("phase:") for note in messages[0].notes)
