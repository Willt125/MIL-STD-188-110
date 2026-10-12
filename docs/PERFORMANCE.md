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

## M8: acquisition and tracking (2026-10-12)

The real receiver (`rx/receiver.py`), with no genie: it finds the preamble, measures carrier
and timing, reads the mode, then tracks. All figures are from the test suite or the scripts
described in VALIDATION.md.

**Preamble detection metric** rho (in-band, so it is the same at 9600 and 48000 S/s):

| Input | rho |
|-------|-----|
| noise only, worst of 10 s (every position and offset bin) | 0.046 |
| preamble at +10 / 0 / −6 / −9 dB SNR | 0.60 / 0.30 / 0.12 / 0.06 |
| threshold | **0.08** (detects to about −7 dB) |

Behind the threshold, the prefix read-back, D1/D2 and count checks reject anything else. With
the threshold lowered to 0.03, they alone rejected all of over 100 noise candidates in 10 s.

**Accuracy (clean signal):** segment timing within 0.1 sample, including fractional delays;
carrier offset within 0.5 Hz anywhere in ±400 Hz (typically 0.02 Hz); transmitter clock offset
learned within about 50 ppm up to ±2000 ppm.

**Impairments decoded perfectly** (test suite): carrier offsets ±400 Hz; clock offsets ±0.2%;
both together; a 3.5 Hz/s Doppler sweep (Appendix C's rate; 10 Hz/s also works, and without
frequency tracking every mode fails at 3.5 Hz/s); 2400S at 10 dB, 1200L at 6 dB, 300S at 0 dB,
75S at −3 dB AWGN; back-to-back messages with and without a gap; lower-sideband input with
`--invert-spectrum`; real-valued audio; 48 kHz.

**Known limits** (M10 work): cubic interpolation of the matched-filter output caps tracked SNR
at about 35 dB (−34 dB worst-case error at the band edge). Speed: acquisition about 17× real
time at 9600 S/s.

## M9: the Appendix E channel simulator, validated (2026-10-12)

`channel/watterson.py` and `m110c-chan`, checked against Appendix E.7. The long averages E.7
asks for (20 minutes to 3 hours) are done on the fading process at its own 32 × d rate, where
3 hours is cheap; the full chain is checked over 1–10 minutes.

| Check | Requirement | Measured |
|-------|-------------|----------|
| E.7.1 flatness at 1050 / 1800 / 2550 Hz (real input, Hilbert transform) | ±0.5 dB | −0.005 / −0.004 / −0.003 dB |
| E.7.2 SNR, fixed path, 0/10/20 dB (60 s) | ±0.25 dB | exact to 0.001 dB |
| E.7.3 mean power through fading, 1 and 2 paths, 1 and 10 Hz (10 min) | ±0.5 dB | −0.05 to +0.11 dB |
| E.7.4 / Table E-I Doppler spectrum, 1 Hz, deviation at −20 dB | ±1.5 dB | −0.35 / −0.25 dB |
| E.7.4 / Table E-I Doppler spectrum, 1 Hz, deviation at −30 dB | ±2.0 dB | +0.17 / +0.27 dB |
| Fading statistics | Rayleigh | P(\|h\|² < 0.1 × mean) within 0.01 of 1 − e^−0.1 |

## Table XVI baseline: the M8 receiver on simulated HF channels (2026-10-12)

Long interleave (§5.3.2.5), the specified SNR, two independent channel seeds per row, about 6 s
of payload each. **This is the starting point for M10, not a result to keep.**

| Rate | Channel | SNR | Required BER | Measured BER | EOM found |
|------|---------|-----|--------------|--------------|-----------|
| 4800 | 1 fixed | 17 dB | ≤ 1e-3 | **0** (48 000 bits) | 2/2 |
| 2400 | 1 fixed | 10 dB | ≤ 1e-5 | **0** (28 800 bits) | 2/2 |
| 4800 | 2 fading, 2 ms, 0.5 Hz | 27 dB | ≤ 1e-3 | 0.68 | 0/2 |
| 2400 | 2 fading, 2 ms, 1 Hz | 18 dB | ≤ 1e-5 | 0.60 | 0/2 |
| 2400 | 2 fading, 2 ms, 5 Hz | 30 dB | ≤ 1e-3 | 0.85 | 0/2 |
| 2400 | 2 fading, 5 ms, 1 Hz | 30 dB | ≤ 1e-5 | 0.60 | 0/2 |
| 1200 | 2 fading, 2 ms, 1 Hz | 11 dB | ≤ 1e-5 | 0.59 | 0/2 |
| 600 | 2 fading, 2 ms, 1 Hz | 7 dB | ≤ 1e-5 | 0.60 | 0/2 |
| 300 | 2 fading, 5 ms, 5 Hz | 7 dB | ≤ 1e-5 | 0.58 | 0/2 |
| 150 | 2 fading, 5 ms, 5 Hz | 5 dB | ≤ 1e-5 | 0.59 | 0/2 |
| 75 | 2 fading, 5 ms, 5 Hz | 2 dB | ≤ 1e-5 | 0.82 | 0/2 |

Two causes, both for M10:
1. **The loss detector fragments transmissions.** A deep fade looks like the end of the signal,
   so one transmission was reported as 3–6 "messages". With loss detection disabled, errors
   drop (for example 300L on 2 ms / 1 Hz at 10 dB: 25–38% instead of 100%), but are still far
   too many.
2. **No equaliser.** A 2–5 ms delay spread smears each symbol over 5–12 neighbours. The
   probe-aided tracker models a single complex gain, which multipath cannot be.

## M10: the equalising receiver on simulated HF channels (2026-10-12)

The same conditions as the baseline above, with ten channel realisations per row instead of
two (about 6 s of payload each; long interleave, 4800 without interleaver).

| Rate | Channel | SNR | Required BER | Measured BER (errors / bits) | Messages in one piece, EOM found |
|------|---------|-----|--------------|------------------------------|----------------------------------|
| 4800 | 1 fixed | 17 dB | ≤ 1e-3 | 8e-6 (2 / 240 000) | 10/10 |
| 4800 | 2 fading, 2 ms, 0.5 Hz | 27 dB | ≤ 1e-3 | 9e-5 (22 / 240 000) | 10/10 |
| 2400 | 1 fixed | 10 dB | ≤ 1e-5 | 0 / 144 000 | 10/10 |
| 2400 | 2 fading, 2 ms, 1 Hz | 18 dB | ≤ 1e-5 | 0 / 144 000 | 10/10 |
| 2400 | 2 fading, 2 ms, 5 Hz | 30 dB | ≤ 1e-3 | 3e-5 (5 / 144 000) | 10/10 |
| 2400 | 2 fading, 5 ms, 1 Hz | 30 dB | ≤ 1e-5 | 0 / 144 000 | 10/10 |
| 1200 | 2 fading, 2 ms, 1 Hz | 11 dB | ≤ 1e-5 | 0 / 72 000 | 10/10 |
| 600 | 2 fading, 2 ms, 1 Hz | 7 dB | ≤ 1e-5 | 0 / 36 000 | 10/10 |
| 300 | 2 fading, 5 ms, 5 Hz | 7 dB | ≤ 1e-5 | 0 / 18 000 | 10/10 |
| 150 | 2 fading, 5 ms, 5 Hz | 5 dB | ≤ 1e-5 | 0 / 8 960 | 10/10 |
| 75 | 2 fading, 5 ms, 5 Hz | 2 dB | ≤ 1e-5 | 0 / 4 800 | 10/10 |

**Every row meets its requirement in these runs.** What this does and does not show:

- Zero errors in 144 000 bits bounds the BER at about 2e-5 (95%), not 1e-5, and the lower
  rates were measured over far fewer bits. Six-second messages also sample few fades at
  0.5–1 Hz. Proper conformance runs (minutes per row, as Appendix E implies) belong to M13.
- 4800 at 17 dB on a fixed path should give BER about 6e-6 for uncoded 8-PSK; 8e-6 is
  consistent with that, so the equaliser costs little when there is nothing to equalise.

### How the design was chosen

The plan was an adaptive decision-feedback equaliser, LMS first and RLS as an option.
Prototypes (genie timing, same channels) ruled that out before any of it reached the package:

| Prototype | 2400, 2 ms, 1 Hz, 18 dB | 2400, 2 ms, 5 Hz, 30 dB | 2400, 5 ms, 1 Hz, 30 dB | 4800, 2 ms, 0.5 Hz, 27 dB |
|-----------|------|------|------|------|
| RLS DFE (fractionally spaced, λ = 0.98–0.99) | 0–7e-4 | ≈ 0.5 | 0 (with 16 postcursor taps) | 5e-3–2e-2 |
| Channel estimate at each probe by plain least squares (17 taps), interpolated; MMSE-DFE computed from it | 3e-4–3e-3 | 4e-2–5e-2 | ≈ 0.5 | — |
| The same, keeping only taps above −20 dB of the delay profile | 0 | 0 | 0 | 8e-4–7e-2 (−25 dB: 5 Hz and 1200 bps fail) |
| The same with an LMMSE estimate (delay-profile prior), as built | 0 | 0 | 0 | 0 |

(Coded BER, two or three channel seeds each, genie timing.) Even a single-tap RLS tracker, with
no multipath at all, gave 17% symbol errors on 5-Hz flat fading at 30 dB (λ = 0.95; 3% only
with a memory of five symbols, λ = 0.8), against 0.3% for a receiver told the true gain: an
adaptive filter learns only from the past, and by the time it has enough symbols to pin down
a dozen taps, the channel has moved. Estimating the channel on both sides
of the data (the probes before and after) and interpolating does not lag. Plain least squares
on 25 taps from 48 observations was too noisy (fixed 10 dB: 27% symbol errors against 6% in
theory); using the long-term delay profile as a prior (LMMSE) brought that back to 6.7%.

Things found on the way, each now covered by a test:

- The delay profile must be learned from a *full-span* estimate. Learned only from the taps
  already in use, a path that was faded during the preamble could never be added.
- A timing detector built on tap *powers* is blind to small errors (the two neighbouring taps
  have equal power either way). The signed, first-order detector of `peak_offset` fixed a
  25% error in the reported clock offset of short bursts.
- Following the delay profile's centroid follows the fades, not the clock: at 1 Hz fading it
  moved the timing by symbols per second and wrecked every 5-ms channel.
- Telling signal from noise needs known symbols. Decided data fits noise as well, so noise
  after a transmission looked like a −1.5 dB signal; the probe correlation does not.
- Single-threaded BLAS: the thousands of 40×40 solves per second ran 50× slower with
  OpenBLAS threads, under load.
- Known symbols are not always pseudo-random. The end of a long preamble repeats every 32
  symbols, which makes the wide least-squares problem singular; the answer then depended on
  the CPU's BLAS kernel. It passed here (AVX-512) and failed in CI (every long-preamble mode
  lost), until a 1e-6 ridge made it well defined. Reproduce kernel differences locally with
  `OPENBLAS_CORETYPE=Haswell`.

### Speed

On the real recordings (one core, 9600 S/s): 0.1–0.4 s of computing per second of signal,
against 0.05–0.15 for the M8 receiver. Pipe-able in real time.

