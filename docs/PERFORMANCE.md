# Performance

Measured results, as opposed to the pass/fail thresholds in the tests. Each section says which
receiver produced it. Figures are reproducible from the scripts named.

SNR is measured as in Table XVI of the spec: signal and noise power both in a 3-kHz bandwidth.
At 2400 symbols/s, the SNR per symbol (Es/N0) is SNR × 3000/2400, i.e. +0.97 dB.

## M7: genie-aided receiver on AWGN (2026-10-12)

The receiver is *told* the timing, carrier phase, gain and mode (`rx/genie.py`), so these are
the best case for the decoding chain alone; acquisition and tracking losses come later (M8–M10).

**Uncoded 4800 bps against theory.** For 8-PSK with Gray-like labelling,
BER ≈ (2/3)·Q(√(2·Es/N0)·sin(π/8)). 120 000 bits per point:

| SNR (3 kHz) | Measured BER | Theory |
|-------------|--------------|--------|
| 11 dB | 1.03 × 10⁻² | 1.06 × 10⁻² |
| 14 dB | 9.0 × 10⁻⁴ | 8.1 × 10⁻⁴ |
| 17 dB | 0 | 6 × 10⁻⁶ |

Agreement within about 10% checks the demapper, the MGD bit labels and the SNR definition at once.

**Coded modes: the rate ladder.** Bit errors in 16 000 bits (2400–600 bps) or 1 600 bits
(150, 75 bps); "–" = message lost (no EOM found):

| Mode | +6 dB | +3 dB | 0 dB | −3 dB | −6 dB |
|------|-------|-------|------|-------|-------|
| 2400L | 0 | – | – | – | – |
| 1200L | 0 | 0 | – | – | – |
| 600L | 0 | 0 | 0 | 633 | – |
| 150S | 0 | 0 | 0 | 0 | 0 |
| 75S | 0 | 0 | 0 | 0 | 0 |

At 150 bps, the 4× repetition is what buys the margin. At −6 dB, 10/10 messages decode with
the copies combined, and 0/10 using one copy.

**Table XVI, single fixed path (AWGN) rows:** 4800 bps at 17 dB (≤ 1e-3) and 2400 bps at
10 dB (≤ 1e-5) are both met, with zero errors in 48 000 bits. The fading rows need the channel
simulator (M9) and an equalizer (M10).
