"""Tests for descrambling and soft demapping (M7)."""

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from oracle import soft_demap as oracle

from m110c import scrambler, symbols
from m110c.rx import demap
from m110c.spec import mode
from m110c.spec import tables as T


def phasor(tribits):
    return np.exp(1j * np.pi / 4 * np.asarray(tribits, dtype=float))


@given(st.lists(st.integers(0, 7), min_size=1, max_size=50), st.integers(0, 500))
def test_descramble_undoes_scrambling(tribits, start):
    seq = scrambler.data_sequence(start, len(tribits))
    sent = phasor(scrambler.scramble(np.array(tribits, np.uint8), seq))
    assert np.allclose(demap.descramble(sent, seq), phasor(tribits))


@pytest.mark.parametrize("n", [1, 2, 3])
def test_noiseless_llr_signs_recover_the_channel_bits(n):
    labels = symbols.tribit_labels(n)
    tribits = np.array(sorted(labels))
    llr = demap.soft_bits(phasor(tribits), n).reshape(-1, n)
    decided = (llr < 0).astype(int)
    assert decided.tolist() == [list(labels[t]) for t in tribits]


@pytest.mark.parametrize("n", [1, 2, 3])
@settings(max_examples=30, deadline=None)
@given(data=st.data())
def test_max_log_llrs_match_the_literal_oracle(n, data):
    re = data.draw(st.lists(st.floats(-2, 2), min_size=1, max_size=20))
    im = data.draw(st.lists(st.floats(-2, 2), min_size=len(re), max_size=len(re)))
    y = np.array(re) + 1j * np.array(im)
    noise_var = data.draw(st.floats(0.05, 3))
    expected, _ = oracle.llrs(y.tolist(), n, noise_var)
    assert np.allclose(demap.soft_bits(y, n, noise_var), expected)


@pytest.mark.parametrize("n", [1, 2, 3])
def test_max_log_agrees_with_exact_llr_sign_at_moderate_noise(n):
    """Max-log is an approximation; it must at least agree in sign almost always."""
    rng = np.random.default_rng(1)
    y = phasor(rng.integers(0, 8, 300)) + 0.3 * (
        rng.standard_normal(300) + 1j * rng.standard_normal(300)
    )
    approx = demap.soft_bits(y, n, 0.18)
    _, exact = oracle.llrs(y.tolist(), n, 0.18)
    assert np.mean(np.sign(approx) == np.sign(exact)) > 0.99


def test_llr_scale_follows_noise_variance():
    y = phasor([1, 6]) * 0.9
    assert np.allclose(demap.soft_bits(y, 3, 0.5), 2 * demap.soft_bits(y, 3, 1.0))


@pytest.mark.parametrize("interleave", ["short", "long"])
def test_75bps_set_correlation_picks_the_sent_pattern(interleave):
    m = mode(75, interleave)
    period = m.exceptional_set_period
    values = np.array([0, 1, 2, 3] * (period // 4 + 1))[:period]  # one block, ends exceptional
    channel_bits = np.array(
        [b for v in values for b in symbols.mgd.mgd_decode(np.array([v]), 2)], np.uint8
    )
    sent = phasor(symbols.data_sets_75bps(channel_bits, m, 0))
    corr = demap.set_correlations(sent, m, 0)
    assert np.argmax(corr, axis=1).tolist() == values.tolist()
    assert np.allclose(corr.max(axis=1), T.TRIBITS_PER_75BPS_SET)  # full energy
    llr = demap.soft_bits_75(corr)
    assert (llr < 0).astype(int).tolist() == channel_bits.tolist()


def test_75bps_wrong_set_position_loses_the_exceptional_set():
    """INT-010 matters to the receiver: correlating an exceptional set against the normal
    patterns (wrong set index) finds nothing."""
    m = mode(75, "short")
    bits = np.zeros(2 * 45, np.uint8)
    sent = phasor(symbols.data_sets_75bps(bits, m, 0))[44 * 32 :]  # the exceptional set
    right = demap.set_correlations(sent, m, 44)
    wrong = demap.set_correlations(sent, m, 0)
    assert right.max() == pytest.approx(32) and abs(wrong).max() < 1e-9


def test_75bps_metric_is_coherent():
    """With known phase the metric is Re{correlation}: an anti-correlated pattern counts
    against itself, it is not evidence for itself (as |correlation| would make it)."""
    m = mode(75, "short")
    patterns = [phasor(T.TABLE_X_NORMAL[v]) for v in range(4)]
    y = patterns[0] - 0.6 * patterns[1]
    corr = demap.set_correlations(y, m, 0)[0]
    assert corr[0] == pytest.approx(32) and corr[1] == pytest.approx(-19.2)
