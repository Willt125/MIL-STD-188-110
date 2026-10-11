"""Tests for the convolutional encoder, repetition and Viterbi decoder (M2)."""

import itertools
import time

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from oracle import conv_encoder as oracle_encoder
from oracle import ml_decoder as oracle_ml

from m110c import fec

bit_lists = st.lists(st.integers(0, 1), max_size=300)


# --- Encoder ------------------------------------------------------------------


def test_int_002_tap_vectors_are_octal_133_and_171():
    """INT-002: the literal reading of Figure 4 gives the commonly quoted pair.

    Written MSB = delay 0: T1 = 1011011 = 0o133, T2 = 1111001 = 0o171."""
    as_int = lambda taps: int("".join(map(str, taps)), 2)  # noqa: E731
    assert as_int(fec.TAPS_T1) == 0o133
    assert as_int(fec.TAPS_T2) == 0o171


def test_impulse_response_is_the_tap_vectors():
    """A single 1 followed by zeros plays out each generator's taps, delay by delay."""
    out = fec.encode(np.array([1, 0, 0, 0, 0, 0, 0]))
    assert out[0::2].tolist() == fec.TAPS_T1.tolist()
    assert out[1::2].tolist() == fec.TAPS_T2.tolist()


@given(bit_lists)
def test_encoder_matches_literal_figure_4_oracle(bits):
    assert fec.encode(np.array(bits, dtype=np.uint8)).tolist() == oracle_encoder.encode(bits)


@given(bit_lists, st.lists(st.integers(1, 40), min_size=1, max_size=20))
def test_encoder_is_chunk_invariant(bits, chunk_sizes):
    """Streaming: splitting the input anywhere must not change the output."""
    enc = fec.ConvolutionalEncoder()
    pieces, i = [], 0
    for size in itertools.cycle(chunk_sizes):
        if i >= len(bits):
            break
        pieces.append(enc.encode(np.array(bits[i : i + size], dtype=np.uint8)))
        i += size
    pieces.append(enc.encode(np.zeros(0, dtype=np.uint8)))
    assert np.concatenate(pieces).tolist() == oracle_encoder.encode(bits)


@given(bit_lists, bit_lists)
def test_encoder_is_linear_over_gf2(a, b):
    n = min(len(a), len(b))
    a = np.array(a[:n], dtype=np.uint8)
    b = np.array(b[:n], dtype=np.uint8)
    assert np.array_equal(fec.encode(a ^ b), fec.encode(a) ^ fec.encode(b))


def test_six_zero_bits_return_encoder_to_state_zero():
    """Why the coder flush works: any state is cleared by six zero inputs."""
    rng = np.random.default_rng(1)
    enc = fec.ConvolutionalEncoder()
    enc.encode(np.ones(6, dtype=np.uint8))
    assert enc.state == 0b111111
    enc.encode(rng.integers(0, 2, 50))
    enc.encode(np.zeros(6, dtype=np.uint8))
    assert enc.state == 0


def gf2_poly_gcd(a: int, b: int) -> int:
    """GCD of two GF(2) polynomials given as bit masks."""
    while b:
        while a and a.bit_length() >= b.bit_length():
            a ^= b << (a.bit_length() - b.bit_length())
        a, b = b, a
    return a


def test_code_is_not_catastrophic():
    """A rate-1/2 code is catastrophic iff its generators share a factor (Massey-Sain).
    A catastrophic code would turn a few channel errors into unbounded decoding errors."""
    g1 = int("".join(map(str, fec.TAPS_T1)), 2)
    g2 = int("".join(map(str, fec.TAPS_T2)), 2)
    assert gf2_poly_gcd(g1, g2) == 1


def test_free_distance_is_10():
    """d_free: the lightest codeword that leaves state 0 and returns. Searching all
    inputs that start with 1, up to 12 bits, plus the zero tail, is enough to find it
    (and to show nothing lighter exists among them) for this code."""
    best = min(
        int(fec.encode(np.array([1, *rest, 0, 0, 0, 0, 0, 0], dtype=np.uint8)).sum())
        for n in range(12)
        for rest in itertools.product((0, 1), repeat=n)
    )
    assert best == 10


def test_trellis_branch_signs_are_plus_or_minus_one():
    """Regression: uint8 arithmetic once turned the -1 for an output bit of 1 into 255."""
    assert set(np.unique(fec.SIGN1)) == {-1.0, 1.0}
    assert set(np.unique(fec.SIGN2)) == {-1.0, 1.0}


@pytest.mark.parametrize("state", range(fec.NUM_STATES))
def test_trellis_agrees_with_encoder(state):
    """Each trellis branch must emit what the encoder emits from that state."""
    history = [(state >> (fec.MEMORY - 1 - i)) & 1 for i in range(fec.MEMORY)][::-1]
    for ns in range(fec.NUM_STATES):
        for b in range(2):
            if fec.PREDECESSOR[ns, b] != state:
                continue
            u = ns >> (fec.MEMORY - 1)
            enc = fec.ConvolutionalEncoder()
            enc.encode(np.array(history, dtype=np.uint8))
            assert enc.state == state
            t1, t2 = enc.encode(np.array([u], dtype=np.uint8))
            assert (fec.SIGN1[ns, b], fec.SIGN2[ns, b]) == (1 - 2 * int(t1), 1 - 2 * int(t2))
            assert enc.state == ns


# --- Repetition -----------------------------------------------------------------


def test_int_008_pairs_are_repeated_as_units():
    coded = np.array([1, 0, 0, 1])
    assert fec.repeat_pairs(coded, 2).tolist() == [1, 0, 1, 0, 0, 1, 0, 1]
    assert fec.repeat_pairs(coded, 1).tolist() == coded.tolist()


@given(bit_lists.filter(lambda b: len(b) % 2 == 0), st.sampled_from([1, 2, 4]))
def test_combine_inverts_repeat(bits, reps):
    soft = fec.hard_to_soft(np.array(bits))
    combined = fec.combine_pairs(fec.repeat_pairs(soft, reps), reps)
    assert np.allclose(combined, reps * soft)


def test_combine_rejects_partial_groups():
    with pytest.raises(ValueError):
        fec.combine_pairs(np.zeros(6), 2)


# --- Viterbi decoder --------------------------------------------------------------


def encode_terminated(message) -> np.ndarray:
    return fec.encode(np.concatenate([np.asarray(message, dtype=np.uint8), np.zeros(6, np.uint8)]))


def decode_terminated(soft, n, **kwargs) -> list[int]:
    return fec.viterbi_decode(soft, **kwargs)[:n].tolist()


@given(bit_lists)
def test_noiseless_round_trip(message):
    soft = fec.hard_to_soft(encode_terminated(message))
    assert decode_terminated(soft, len(message)) == message


@settings(max_examples=60, deadline=None)
@given(st.integers(1, 10), st.integers(0, 2**32 - 1), st.floats(0.3, 2.0))
def test_viterbi_matches_brute_force_maximum_likelihood(n, seed, noise):
    """The decoder must find exactly the ML message, at any noise level, including
    levels where the answer is wrong. Correctness, not just good BER."""
    rng = np.random.default_rng(seed)
    message = rng.integers(0, 2, n)
    soft = fec.hard_to_soft(encode_terminated(message)) + noise * rng.standard_normal(2 * (n + 6))
    assert decode_terminated(soft, n) == oracle_ml.decode(soft.tolist(), n)


@settings(max_examples=200, deadline=None)
@given(st.data())
def test_corrects_any_four_hard_errors(data):
    """With d_free = 10, any pattern of at most 4 bit errors is within the decoding
    radius of a terminated codeword."""
    message = data.draw(st.lists(st.integers(0, 1), min_size=1, max_size=60))
    coded = encode_terminated(message)
    k = data.draw(st.integers(0, 4))
    positions = data.draw(
        st.lists(st.integers(0, len(coded) - 1), min_size=k, max_size=k, unique=True)
    )
    coded[positions] ^= 1
    assert decode_terminated(fec.hard_to_soft(coded), len(message)) == message


def test_erasures_carry_no_information_but_do_not_break_decoding():
    """Soft value 0 = erasure. Erasing every 4th coded bit (like puncturing to rate
    2/3) still decodes noiselessly."""
    rng = np.random.default_rng(2)
    message = rng.integers(0, 2, 500)
    soft = fec.hard_to_soft(encode_terminated(message))
    soft[3::4] = 0.0
    assert decode_terminated(soft, 500) == message.tolist()


def test_unknown_start_state_recovers_after_a_few_constraint_lengths():
    """Late entry: start decoding mid-stream with no idea of the encoder state."""
    rng = np.random.default_rng(3)
    message = rng.integers(0, 2, 400)
    soft = fec.hard_to_soft(encode_terminated(message))
    tail = soft[200:]  # starts in an arbitrary state
    decoded = fec.viterbi_decode(tail, start_state=None)[: 400 - 100]
    assert decoded[50:].tolist() == message[150:].tolist()


@settings(max_examples=30, deadline=None)
@given(st.integers(0, 2**32 - 1), st.lists(st.integers(1, 97), min_size=1, max_size=8))
def test_viterbi_is_chunk_invariant(seed, chunk_sizes):
    """Output must not depend on how soft bits are split across calls, including
    splits inside a T1/T2 pair (odd chunk sizes)."""
    rng = np.random.default_rng(seed)
    message = rng.integers(0, 2, 300)
    soft = fec.hard_to_soft(encode_terminated(message)) + 0.8 * rng.standard_normal(612)
    reference = fec.viterbi_decode(soft, traceback_depth=20)
    dec = fec.ViterbiDecoder(traceback_depth=20)
    pieces, i = [], 0
    for size in itertools.cycle(chunk_sizes):
        if i >= soft.size:
            break
        pieces.append(dec.decode(soft[i : i + size]))
        i += size
    pieces.append(dec.flush())
    assert np.array_equal(np.concatenate(pieces), reference)


def test_viterbi_memory_is_bounded():
    dec = fec.ViterbiDecoder(traceback_depth=40)
    rng = np.random.default_rng(4)
    for _ in range(50):
        dec.decode(rng.standard_normal(2 * 37))
        assert len(dec._decisions) < 2 * 40


def test_viterbi_releases_bits_with_bounded_delay():
    """A pipe needs output to flow before end of input: at most 2*depth bits pending."""
    dec = fec.ViterbiDecoder(traceback_depth=40)
    out = dec.decode(fec.hard_to_soft(fec.encode(np.zeros(1000, dtype=np.uint8))))
    assert 1000 - 2 * 40 <= out.size <= 1000


def test_flush_resets_for_next_message():
    dec = fec.ViterbiDecoder(traceback_depth=16)
    for message in ([1, 0, 1, 1], [0, 1, 1, 0, 0, 1]):
        soft = fec.hard_to_soft(encode_terminated(message))
        bits = np.concatenate([dec.decode(soft), dec.flush()])
        assert bits[: len(message)].tolist() == message


def test_flush_rejects_dangling_half_pair():
    dec = fec.ViterbiDecoder()
    dec.decode(np.ones(3))
    with pytest.raises(ValueError):
        dec.flush()


def test_truncated_traceback_matches_full_block_decoding():
    """Depth 96 should be indistinguishable from keeping the whole trellis."""
    rng = np.random.default_rng(5)
    message = rng.integers(0, 2, 3000)
    soft = fec.hard_to_soft(encode_terminated(message)) + 0.9 * rng.standard_normal(6012)
    streaming = fec.viterbi_decode(soft, traceback_depth=96)
    full = fec.viterbi_decode(soft, traceback_depth=10_000)
    assert np.array_equal(streaming, full)


def awgn_llr(coded, ebn0_db, rate, rng):
    """BPSK over AWGN: return LLRs for the coded bits at the given Eb/N0."""
    esn0 = rate * 10 ** (ebn0_db / 10)
    sigma2 = 1 / (2 * esn0)
    y = fec.hard_to_soft(coded) + np.sqrt(sigma2) * rng.standard_normal(coded.size)
    return 2 * y / sigma2


def test_coding_gain_on_awgn():
    """At Eb/N0 = 4 dB uncoded BPSK has BER about 1.25e-2; this soft-decision K=7 code
    should be below 1e-3 (textbook value is around 1e-5)."""
    rng = np.random.default_rng(6)
    message = rng.integers(0, 2, 20_000)
    llr = awgn_llr(encode_terminated(message), 4.0, 0.5, rng)
    errors = np.count_nonzero(fec.viterbi_decode(llr)[:20_000] != message)
    assert errors / 20_000 < 1e-3


def test_repetition_improves_decoding():
    """Same Es/N0 per channel bit: 4x repetition (150 bps) must beat no repetition."""
    rng = np.random.default_rng(7)
    message = rng.integers(0, 2, 4000)
    coded = encode_terminated(message)
    sigma = 1.3
    plain = fec.hard_to_soft(coded) + sigma * rng.standard_normal(coded.size)
    rep = fec.hard_to_soft(fec.repeat_pairs(coded, 4))
    rep = fec.combine_pairs(rep + sigma * rng.standard_normal(rep.size), 4)
    err_plain = np.count_nonzero(fec.viterbi_decode(plain)[:4000] != message)
    err_rep = np.count_nonzero(fec.viterbi_decode(rep)[:4000] != message)
    assert err_rep < err_plain


def test_decoder_is_faster_than_real_time():
    """The fastest coded mode (2400 bps) needs 2400 decoded bits per second.

    Measured on a development machine: about 23x real time. The assertion asks for 4x,
    loose enough not to flake on slow CI runners but tight enough to catch a regression
    such as an accidentally de-vectorised add-compare-select."""
    soft = fec.hard_to_soft(fec.encode(np.zeros(2400, dtype=np.uint8)))
    start = time.perf_counter()
    fec.viterbi_decode(soft, terminated=False)
    assert time.perf_counter() - start < 0.25
