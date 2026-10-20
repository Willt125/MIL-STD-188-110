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

## M11: acquisition and tracking in fast fading (2026-10-12)

M10 met Table XVI with long preambles. M11 measured the next weak points: short preambles on
fading channels, and 75 bps at and below its Table XVI SNR. Both turned out to be
acquisition problems; M11 changes only the acquisition.

### Short-preamble acquisition

Short interleave (0.6-s preamble), each Table XVI fading channel at its specified SNR, 20
transmissions per row:

| Rate | Channel | SNR | Acquired, M10 | Acquired, M11 | On the first segment, M10 → M11 |
|------|---------|-----|---------------|---------------|-------------------------------|
| 2400 | 2 ms, 5 Hz | 30 dB | 18/20 | 20/20 | 10 → 15 |
| 300 | 5 ms, 5 Hz | 7 dB | 18/20 | 20/20 | 8 → 15 |
| 150 | 5 ms, 5 Hz | 5 dB | 18/20 | 20/20 | 8 → 15 |
| 75 | 5 ms, 5 Hz | 2 dB | 16/20 | 19/20 | 3 → 11 |
| other fading rows | | | 20/20 | 20/20 | 20 → 20 |

The cause was in reading a detected segment, not in detecting it: true segments were found
with a healthy metric and a correct prefix, then rejected because the count slots were
misread. Slots were decided coherently against a phase ramp fitted to the prefix and
extrapolated up to 190 ms; on a 5-Hz channel the phase does not follow a ramp that long.
Each slot after the prefix is now judged against the phase of the slot before it. Two other
fixes were tried and rejected: non-coherent slot decisions (28/30 instead of 25/30 at 75 bps,
but fewer detections at −9 dB on AWGN) and choosing the most likely *valid* segment ending
(worse: without the validity check as a filter, misaligned candidates were accepted, and
the demodulator spent its loss window on them while the real preamble went by). On AWGN near
the detection limit the phase-tracked reading detects as often as before (of 60
transmissions, before and after: 2400 bps at −8 dB 42 and 42, at −7 dB 58 and 59; 600 bps
at −8 dB 41 and 43; 75 bps at −8 dB 45 and 45).

### Counts misread in a fade

The rest of the 75-bps losses at the Table XVI point were acquisition too. 75L on 2 paths,
5 ms, 5 Hz, at 2 dB: three of 16 channels lost their message, each the same way. The first
segment was detected at the right place with a correct prefix, but a fade turned its count
slots into another *valid* count (8 instead of 23, say). The data phase was then expected
in the middle of the preamble, the demodulator produced a second of garbage until it
declared the signal lost, and the search found the preamble again further on and decoded
the real message after the garbage. The old reading did the same on these channels;
phase-tracked reading does not cause it, and no slot reading can avoid it in a deep fade.

A count is now checked against the next segments, which must count down from it: the first
of the next two that reads validly decides, and a contradicted count is rejected (the
search goes on and finds a later segment). A segment that only confirms may have three
prefix slots wrong rather than one, since its position is already known. The search waits
0.4 s longer before it reports a detection; the receiver replays the buffered samples, so
nothing is lost.

75L messages decoded with no errors, of 16, on that channel:

| SNR | M10 | M11 |
|-----|-----|-----|
| 2 dB (Table XVI) | 13 | 16 |
| 0 dB | 15 | 15 |
| −1 dB | 7 | 8 |

Below 0 dB the failures are partial (a few hundred bit errors in a decoded message): the
75-bps demodulator, not acquisition, is the limit there. The short-preamble table above is
unchanged with the check, and on AWGN near the detection limit it detects exactly as often
(60 transmissions each: 2400 bps at −8 and −7 dB, 600 and 75 bps at −8 dB).

### Tracking at 75 bps: investigated, nothing kept

M11 also tried to move the 75-bps limit by changing the equaliser's tracking. Nothing
survived measurement against M10 (16 channels per point, so differences of two or three
are noise), and the equaliser is unchanged. Recorded because each looked convincing:

- *An ambiguity check.* The end of a long preamble repeats every 32 symbols, so in the wide
  estimate (taps −20…20) a path at +12 cannot be told from one at −20; such windows were
  detected (smallest eigenvalue of SᴴS) and kept out of the delay profile, timing and
  alignment. 26 of 48 decoded at 0, −1 and −2 dB, against M10's 23: not significant. A path
  12 symbols late, which is what the ambiguity could confuse, is tracked correctly by M10
  already (a test now guards it).
- *Shortening the wide span below 32 taps,* to make the ambiguity impossible: it can no
  longer see a path 12 symbols early, which is the 5-ms channel when acquisition locks onto
  the later path.
- *Coasting through fades* (loops and realignment learning only from units with at least
  a tenth of the power as signal): 10 or 11 of 16 at −1 dB either way (with the ambiguity
  check in place). With a dead gap in a
  75-bps message (0.3 to 0.6 s, clock offsets up to ±600 ppm, 20 cases) M10 decoded 19
  and the gated loops 20, also not enough to keep it. Gaps of 0.8 s, most of the 1-s loss
  window, end the message with either.
- *Weighting each loop correction by its estimated reliability:* no gain at 75 bps, and the
  2400-bps, 2-ms, 5-Hz row fell to BER 2e-2. At 5-Hz fading the channel changes within the
  estimation window, the residual looks like noise even at 30 dB, and the weights throttled
  the carrier loop while the acquired carrier was still several hertz off.
- *A new timing detector* (the strongest path's offset unwrapped, or a power-weighted
  average over all paths): short-interleave 2400 bps on the 2-ms, 5-Hz channel decoded 12
  of 20 messages instead of 17.

Remaining limit at 75 bps: at 5-Hz Doppler the channel is not constant over the estimation
windows (40–68 ms), so even without noise the fit is imperfect in places. Shorter windows
would trade that for more noise. Not pursued: the Table XVI point now decodes every channel
measured.

### Table XVI after M11

Four channels per row, as in the M10 regression run: every row still meets its requirement
(the only bit errors: 4800 bps on 2 ms, 0.5 Hz, BER 2e-5, and 2400 bps on 2 ms, 5 Hz,
BER 3e-5, each from one channel, against 1e-3). Clock offsets of ±900 and ±2000 ppm are
still reported within 20 ppm, and short-interleave 2400 bps on the 2-ms, 5-Hz channel
decodes 17 of 20 messages, as in M10.


## M12: streams, garbage and back-to-back messages (2026-10-13)

### Speed: BLAS threads

The equaliser solves thousands of small (about 40 × 40) linear systems per second. With
OpenBLAS's default threads, each wakes several threads for a few microseconds of work. On a
loaded 4-core machine the receiver then ran **3.5–4× slower than real time** (60 s of 2400S
took 232 s of CPU). M10 had set `OPENBLAS_NUM_THREADS=1` in the package's `__init__`, but an
environment variable only works if it is set before NumPy is first imported, and a program
that imports NumPy before `m110c` (the normal order) got the slow receiver. The command-line
tools were not affected. The limit is now applied around each receiver call with
`threadpoolctl` (a new dependency; about 6 µs per call), and the caller's own setting is
restored afterwards. A test checks that the solves run on one thread while the caller allows
two.

One CPU core, 60 s of signal fed in 0.1-s chunks, NumPy imported first:

| Mode | CPU per second of signal |
|------|--------------------------|
| 4800 | 0.29 s |
| 2400S / 2400L | 0.33 / 0.30 s |
| 600S | 0.28 s |
| 75S / 75L | 0.12 / 0.10 s |

`m110c-tx | m110c-chan --preset "2 fading 2 ms 1 Hz" --snr 18 | m110c-rx` on 65 s of 2400L
(18000 random bytes) took 24 s of wall time, and the output was identical to the input.

### Latency in a live stream

When does each byte come out, relative to when it was on the air? (Input in 0.1-s chunks;
20 s of data per mode.)

| Mode | Median | Maximum |
|------|--------|---------|
| 4800 | 1.1 s | 1.2 s |
| 2400S | 1.5 s | 1.8 s |
| 2400L | 3.7 s | 6.0 s |
| 600S | 2.0 s | 2.3 s |
| 75S | 5.9 s | 6.9 s |
| 75L | 7.8 s | 10.6 s |

Three things add up: the deinterleaver (a byte waits for the end of its interleaver block:
0.6 s short, 4.8 s long), the demodulator's one-second loss window (it holds output back
while it judges whether the signal is gone, so that a lost signal's last units are not
decoded as data), and the end-of-message check (INT-018), which holds back the 32 + 144 + 31
bits it needs to confirm an EOM against its flush: 0.09 s at 2400 bps, 2.8 s at 75 bps.
All three are inherent to the design; none was changed.

### Memory over long streams

Ten minutes of noise, a 200-s 2400L message, noise, a 200-s 75S message and noise ran in
flat memory (about 20 MB traced by Python), after two fixes:

- Every equaliser unit's SNR was kept for the whole message: about 2.5 kB a second, 9 MB an
  hour. Only the most recent 1000 are kept now (they are a debugging aid; nothing reads
  them).
- At each message end a new preamble search was built while the old one was still
  referenced. Each holds about 17 MB of precomputed correlation FFTs (33 frequency bins × 4
  waveforms × 8192 points), so the peak briefly doubled that. The old one is now released
  first.

Both are covered by tests (the second by the slow 10-minute test).

### Garbage input

Random bytes read as cf32 contain NaN, infinities and values up to 3.4e38. Hypothesis
fuzzing of the receiver (arbitrary bytes, random chunking, corrupt samples inside a real
transmission, truncation anywhere), plus 300 longer random cases, found:

- NaN samples raised a NumPy warning when cf32 was decoded; non-finite samples now become
  silence at the receiver's input. A message with NaN and infinite samples inside it still
  decodes.
- One 3.4e38 sample could overflow the search metric (a warning). That block's metric is
  meaningless anyway: the preamble segment sharing it is missed and a later one is found.
- A WAV header with no channels, or a 0-Hz sample rate (which passed the multiple-of-2400
  check), ended in a traceback; it is now a clean error.

After the fixes: no exception or warning in any case, and a message between two blocks of
random garbage was found 100 times in 100. The receiver works for any input amplitude from
1e-15 to 1e9 (a scale of 1e-18 and below fails; cf32 from a real source is never that
small).

### Back-to-back messages at different rates

With no gap between two transmissions, one of them at 75 bps and the other not, the second
was never received. The end of a message was taken from where the demodulator declared loss
of signal; a 75-bps demodulator finds a following 2400-bps preamble a plausible signal (and
the other way round), so loss came up to a second late, and the search for the next message
started past most of its 0.6-s preamble. The end is now computed from the EOM: the number of
payload bits before it gives the length of the data phase by the framer's arithmetic
(`data_phase_symbols`, tested against the transmitter for every mode and length). The same
end corrects the reported SNR, which had included the following transmission as noise (a
clean 2400S message reported −3.8 dB; now 61 dB).

A second fault showed up with it: when the input ended while a message was in progress,
`finish()` closed that message but never searched the samples after it, so a short second
transmission at the very end of a file was lost. Every pairing of 2400S, 75S, 4800, 1200L,
300S and 75L now decodes, with the input ending at once and with silence after it (72 cases).

### Real recordings

All 20 local recordings decode exactly as with the M11 receiver (the same messages, modes,
byte counts and EOMs, compared file by file). They are now acceptance tests
(`tests/acceptance/`, 17 cases), run when `M110C_RECORDINGS` names a directory holding them
at 9600 S/s; they pass in about two minutes.

### Diagnostics

`m110c-rx` now prints a line when each message's preamble is found (mode, time, carrier),
before the summary when it ends, so a live listener knows something is being received.

## M13: conformance at length, and 75 bps over long messages (2026-10-13)

### The conformance report

`tools/conformance.py` runs every Table XVI row through the Appendix E simulator and the
complete receiver, over many independent channel realisations, and writes
[`CONFORMANCE.md`](CONFORMANCE.md). The rows that require 1e-5 get a million bits each, so
that with no errors the 95% upper bound on the BER is a third of the requirement. Earlier
milestones ran each row for a few thousand bits; this is the first measurement able to
confirm the 1e-5 rows rather than merely fail to contradict them.

### 75 bps lost lock on long messages

The tool's first trial run failed the 75-bps row: one of twelve one-minute messages lost
lock 48 s in. The short messages of earlier milestones (about 6 s of data) had hidden it;
losing lock is a rate per minute, and on one-minute messages at 0 dB fewer than half
survived (22 of 48), against 15 of 16 short ones.

A trace of the failing message showed the mechanism. The channel has no clock offset, so the
symbol period is exactly 4 samples. Through most of the message the timing loop's period
wandered by about ±0.04%. Then, over a few weak units, it absorbed a consistent error of
about 1.5 symbols per unit, which is not large enough to be treated as a switch to the other
path, fell to 3.9926 (0.18% off), and ran 15 symbols away within two seconds. At 75 bps there
are no probes: the channel estimate comes from decided symbols, and once timing has gone, so
have the decisions.

What was measured (one-minute 75L messages, 5 ms, 5 Hz, 0 dB; clean of 48):

| Change | Clean |
|--------|-------|
| none (M12) | 22 |
| units with under 10% of the power as signal teach no loop | 31 |
| timing error clipped to ±½ symbol per unit | 18 |
| clipping and the gate | 23 |
| period adapts 10× more slowly in the data phase | 36 |
| slower period and the gate | 37 |
| period frozen after the preamble | 30 |
| frozen period and the gate | 41 |

Clipping is worse: real corrections can be large. Freezing the period scored best, but
relies entirely on what a 0.6-s preamble taught it, and differs from the slower period by
less than the run-to-run spread; the slower period with the gate was kept. Applied at all
rates it made 4800 bps slightly worse on the same channels (236 bit errors against 192 on
the 0.5-Hz row), and nothing was measured to gain from it there, so it applies at 75 bps
only. The final version (gate only in the data phase), on other channels than the ones it
was chosen on, clean of 48:

| | M12 | M13 |
|-|-----|-----|
| 75L, 2 dB (Table XVI) | 48 | 48 |
| 75L, 0 dB | 22 | 33 |
| 75S, 2 dB | 44 | 42 |
| 75S, 0 dB | 6 | 18 |

Clock offsets of ±900 and ±2000 ppm are followed as before (75S and 2400 bps, AWGN and
fading, one-minute messages: the same messages decode, clocks reported within 20 ppm), the
real recordings decode as before, and the message that failed is a slow test.

### The rest of the 75-bps failures

The full conformance run after the period fix still lost 3 of 223 one-minute 75L messages
at the Table XVI point. Traced one by one, they had two other causes:

- **Phantom paths.** In a deep fade the sampling phase drifted by tens of samples, and the
  alignment then jumped 15–20 symbols in one step, further than any real path (the two are
  12 symbols apart): the decision-directed delay profile, built from symbols decided while
  the signal was mostly noise, showed a path that was not there.
- **A misread count.** One message failed from its first byte with perfect timing: the
  preamble was found on its second segment, whose count was read as 23 instead of 22 in a
  fade, and the next two segments, which should have contradicted it (M11), were faded
  too. The data phase was placed one segment (0.2 s) late.

**Set-correlation check.** At 75 bps every 32-symbol set is one of four known patterns,
under a scrambler tied to the symbol count, which the receiver never loses. Read at the right
delay a set correlates strongly with one pattern; read at a wrong one, even a whole set away,
the scrambling no longer matches and it correlates with none. So correlating the sets at
every tap gives a delay profile that needs no decisions and no channel estimate. It is
coarse (paths stand about 5 dB above the floor of wrongly-timed sets at 2 dB SNR, where the
decision-directed profile shows 15 dB on average), too coarse to choose the alignment with:
used that way it lost messages that had decoded. Used as a check, it works: a realignment
must land where it sees a path, within 6 dB of its strongest. (This is the "set boundary
sync" of the M11 plan. Exceptional sets are not needed for it: they would say which set of
the interleaver block is which, and the symbol count already does.)

**Counts confirmed before use.** A long-preamble count that later segments can still check
is now only used once one of them confirms it; the search moves on to the next segment
otherwise. A long preamble has 24 segments, so this costs nothing but 0.2 s per rejected
segment; only its last two, and short preambles, go unchecked.

Four changes were measured on fresh channels (one-minute messages, 5 ms, 5 Hz, 0 dB, clean
of 48), all on, then each removed:

| | 75L | 75S |
|-|-----|-----|
| none of the four | 26 | 18 |
| all four | 45 | 11 |
| without the set-correlation check | 35 | 12 |
| without starting the data phase from the preamble's clock fit | 41 | 26 |
| without the fade gate in the preamble too | 44 | 11 |
| without confirmed counts | 44 | 11 |

Starting the data phase from the clock fit hurt 75S badly (a 0.6-s preamble is too short
for a good fit) and was dropped; so was the fade gate in the preamble (no gain at 0 dB, and
at the Table XVI point below one new failure). At the Table XVI point, on 223 fresh
channels (one-minute 75L messages, 2 dB):

| | Clean of 223 |
|-|-------------:|
| M13 period fix only | 220 |
| plus the set-correlation check | 220 (the same three: misread counts) |
| plus confirmed counts | **223** |
| plus the fade gate in the preamble | 222 |

The set-correlation check and confirmed counts were kept. Both failures traced above are
slow tests, as is the first one.

### Conformance

With both, the conformance run meets every row of Table XVI but one, 600 bps, which is
consistent with it: 5 bit errors in a million, a measured BER of 5e-6 whose 95% bound lands
on the 1e-5 requirement. 75 bps: no errors in a million bits over 223 one-minute messages.
That run's channels include the three diagnosed above, so on its own it is partly the data
the fixes were chosen on; the 223 fresh channels above are the independent check.

Losing lock remains how 75-bps messages fail below the Table XVI SNR, at a rate per minute
(75L at 0 dB: 45 of 48 one-minute messages clean). Recovering lock in the data phase, with
no probes, is not attempted.

## S4: telling zero interleave from short interleave (2026-10-18)

Zero and short interleave share their preambles (INT-012), their probes and their frames, so
the demodulator's output is the same under both; only the deinterleaver differs. The
receiver now decodes the first 0.6-s block both ways and compares the two Viterbi fits: the
best path metric over the most any path could score (`ViterbiDecoder.fit`, 1 for soft bits
that are exactly a code sequence). Under the right hypothesis the soft bits are a code
sequence plus noise; under the wrong one they are a code sequence shuffled, which no code
path follows, and they fit like noise. Both decoders see the same soft bits in another order,
so their fits compare directly. (Absolute fits are not comparable across inputs: pure
Gaussian noise fits 0.86, a shuffled noiseless code sequence 0.75.)

### The margin

`python tools/interleave_check.py margins`: every rate, both interleaves sent, a noise-only
channel and 2 paths, 2 ms, 1 Hz fading, -9 to 18 dB, six 4-s messages per point. The margin
is fit(true) minus fit(wrong); "decodable" means a bit error rate below 1e-3 under the true
interleave.

| rate | blocks | decodable | smallest margin | not decodable | smallest | largest |
|-----:|-------:|----------:|----------------:|--------------:|---------:|--------:|
| 2400 | 1 | 49 | +0.088 | 125 | -0.010 | +0.168 |
| 1200 | 1 | 77 | +0.115 | 107 | -0.033 | +0.225 |
| 600 | 1 | 105 | +0.087 | 81 | -0.039 | +0.242 |
| 300 | 1 | 124 | +0.036 | 56 | -0.022 | +0.146 |
| 150 | 1 | 137 | +0.067 | 44 | -0.020 | +0.174 |
| 75 | 1 | 145 | +0.101 | 47 | +0.009 | +0.302 |

After one block, every one of the 637 decodable messages was ahead in the right direction
by 0.036 or more; margins the wrong way (down to -0.039) came only from messages that could
not be decoded under either hypothesis. Two and three blocks change little (the tool prints
them). Hence the rule (`rx/autointerleave.py`): the hypothesis ahead by `DECIDE_MARGIN` =
0.02 after a block wins; with no decision after three blocks, the larger fit. Two shortcuts
come first: an EOM found under one hypothesis decides, and a signal that ends inside its
first block (no EOM) was zero interleave, since short interleave always sends whole blocks.
(Fits from a partial block are not used: the short decoder fills the rest with erasures, and
a few soft bits among erasures fit any code.)

### End to end

`python tools/interleave_check.py end-to-end`: the complete receiver with `interleave="auto"`
against one told the truth, on every Table XVI row below 4800 bps, from 4 dB below to 3 dB
above the row's SNR, both interleaves, eight 10-s messages per point.

**576 messages (347 decodable when told the truth): none received in the wrong mode, none
received worse with auto.** Every decision was made on the margin after the first block.

Cost: no added delay when short interleave is sent (its decoder waits for a whole block
anyway); up to one block (0.6 s) when zero interleave is sent, while its bits are held.
Long interleave and 4800 bps have their own D1/D2 and are not affected; digital voice
(2400V) is short interleave only. `tools/sweep.py` gives the receiver the interleave, so its
data still measures each mode itself.

## S3: any audio sample rate (2026-10-18)

The modulator and the receiver count whole samples per symbol, so they run natively at
multiples of 2400 S/s only. Other rates (8000, 11025, 16000, 22050, 44100) now go through
`m110c.resample`: exact rational resampling to and from 9600 S/s (44100 -> 9600 is 32/147),
by a polyphase Kaiser-windowed sinc that passes 0-3400 Hz and stops, 80 dB down, whatever
would alias into it (from min(rate in, rate out) - 3400 Hz). It adds no delay, and its
output does not depend on how the input is chunked.

Measured: a tone anywhere in 300-3300 Hz comes out within 3e-4 of the ideal at every rate
pair tested; aliases and images are 70 dB or more down (tests/unit/test_resample.py). A
clean 2400S message at 8000 and 44100 S/s reads exactly as at 9600: carrier +0.0 Hz, clock
0 ppm, SNR 53.2 dB (the 16-bit WAV's limit), and the bytes are identical. Receiver CPU per
second of signal (2400L, 20 s, one core): 0.22 s at 9600 S/s, 0.23 s at 8000, 0.24 s at
44100 (resampled), 0.27 s at 48000 (native, 20 samples per symbol).

The receiver's diagnostics count samples at the rate it works at (`Receiver.sample_rate`,
9600 when resampling; `Receiver.input_rate` is the rate given). Below 6800 S/s the
300-3300 Hz signal does not fit and the rate is refused.

## S5: late entry, joining a transmission without its preamble (2026-10-18)

`Receiver(late_entry=True)` (`m110c-rx --late-entry`) also joins transmissions whose
preamble it missed, as the §5.3.2.3.7.1.1 note asks: from "the modification of the known data
at interleaver boundaries", the D1/D2 probes before each block at 150-4800 bps and the
exceptional sets at 75 bps (`rx/lateentry.py`, SPEC_NOTES INT-029). Three steps: a search for
the probes (or 75-bps sets) over +/-400 Hz, which fixes timing, carrier and scrambler phase;
the equalising demodulator in a late mode, which reads each probe (each set) and finds a
block boundary and the mode; and a check of that boundary by decoding the block after it
(the S4 rule: fit against no interleaver). Decoding then starts at that block, with the
encoder state unknown.

### Measurements

`python tools/late_entry_check.py join`: every rate and interleave, the transmission cut at a
random point 0.25 to 2.25 s into its data, at three SNRs on a noise-only channel (8 dB
below the mode's Table XVI point, and 3 dB either side) and on 2 paths, 2 ms, 1 Hz (75 bps:
5 ms, 5 Hz) at and around the Table XVI point, four seeds each: 336 receptions. The bit error
rate is over the end of the message, which both receivers deliver, against a preamble
receiver given the whole transmission.

- **Joins**: 318 of 336, all in the right mode. All 18 misses are on the noise-only
  channel at its two lowest SNRs: 75 bps at -9 dB (8) and -6 dB (1), 150 bps at -6 dB (4)
  and 150L at -3 dB (1), 300L at -4 dB (2), 600L at -4 and -1 dB (one each). Most are
  below where the preamble receiver decodes either; 150L at -3 dB and 600L at -1 dB are
  not (the preamble receiver: no errors, and 6e-4).
- **Signal needed** (from the cut to the start of the first block delivered): 1.4-1.6 s with
  zero or short interleave, at 4800 and at 75S (of which 1 s is the settling time below),
  2-5 s at the lowest SNRs; 3.1-7.4 s with long interleave, 10-13 s near the lowest SNRs
  and once 27 s (the next block boundary is up to 4.8 s away, and a missed D1/D2 costs a
  block).
- **Errors**: where the preamble receiver decodes cleanly, so does the late one, except on
  fading channels: there, in 11 of 288 receptions (a separate run, all rates but 75, eight
  seeds) the late receiver was materially worse (BER above twice the preamble receiver's
  plus 1e-3). The largest gaps in the table, 4800 bps at 27 dB and 2400S at 15 dB fading
  (2e-2 against 1e-3), are one reception each, on the same channel realisation, with the
  errors in the first two seconds after joining. Late is sometimes better: it joins after
  a fade that cost the preamble receiver its message.
- **Noise alone** (`noise --seeds 20`): 20 minutes, 0 bytes, 0 messages.
- **No harm** (`harm`): 56 transmissions with their preambles, every mode, on the fading
  channel at the Table XVI SNR: all 56 received identically with late entry on and off.

### What it took

The first end-to-end runs showed late entry joining reliably and decoding worse. Each gap
came from something the preamble supplies:

- *Probes before the blocks are known.* At first the late demodulator took every probe
  for the zero pattern, and a D1/D2 frame then looked like a deep fade. It now decides each
  probe (zero or D4-D7) on the equalised signal and fits its channel estimate to the probe
  so decided. In a fade that decision can be wrong, and an estimate fitted to the wrong
  probe pulled the timing loop away for good (a 1200S message lost while the preamble
  receiver rode the same fade). So a probe decided by under 3 noise standard deviations
  keeps the previous estimate and trains no loop, for at most two frames (more, and a
  channel that has faded entirely would never be estimated again). Measured on the fading
  run: 11 receptions worse than the preamble receiver with the hold, 22 without.
- *The first channel estimate.* Without a preamble it comes from the probes alone, the
  data counting as noise, so every tap of the wide estimate carries estimation noise. Left
  in, that noise sat in the power-delay profile for seconds as weak paths on every tap, and
  every channel estimate after it was noisier: at 4800 bps on the fading channel, ten
  times the preamble receiver's errors in the first second. Now the estimate's own noise
  is subtracted, and a tap counts only if it clears that noise fourfold (in one case: 644
  errors in the first second down to 47, the preamble receiver 10).
- *The time base.* The search puts symbol 0 on a whole probe period, which can lie before
  the first sample it holds; the demodulator was fed from the first sample held, and its
  time base slipped by the difference: the channel started up to 10 symbols from tap 0.
  The equaliser's realignment usually recovered, but at low SNR it could settle half a
  symbol off (a 600L message at -1 dB: BER 3e-2, the preamble receiver 2e-4; now
  identical). The search now pads with zeros.
- *Settling.* Decoding starts at the first block at least 1 s (2400 symbols) after the
  first channel estimate, so the profile and the loops have settled; without it 2400S on
  the fading channel lost bits in the first two blocks.
- *Asynchronous characters.* At 300, 150 and 75 bps with zero or short interleave a block
  is not a whole number of bytes, so the UART decoder may start mid-character. It now picks
  the start-bit phase that frames best over its first 32 characters; before, 75S joined
  cleanly and delivered garbage.

The rules for the boundary itself held from the prototypes: two consecutive confident D
probes (or, at 75 bps, a confident exceptional set and the spacing of the next ones) and the
decoding check. Two wrong-mode joins in the first run (300L on the fading channel, 75S at
-6 dB) did not recur after the fixes above.

## Roll-off: the transmit filter, the bandwidth and the receiver (2026-10-20)

The pulse shape is not fixed by 110C: §5.3.2.3.9 says only that the waveform "must be
filtered", and 110C limits the occupied bandwidth (99% of the power) to 3 kHz; its
Appendices C and D suggest a root-raised cosine with a roll-off of 0.35. We used 0.25. US
amateur rules limit HF data to 2.8 kHz measured at the points 26 dB down, which 0.25 exceeds
(docs/NETWORK.md, D5). The roll-off is now a setting on both sides: `m110c-tx --rolloff`
(as before) and `m110c-rx --rolloff` / `Receiver(rolloff=...)`, which sets the filters of
the preamble search, the demodulators and late entry.

### Bandwidth

`python tools/rolloff_check.py bandwidth`: a 2400S message at audio; -26 dB relative to the
mean power density over 600-3000 Hz.

| Roll-off | 99% power | -26 dB | -26 dB, filter span 16 |
|---:|---:|---:|---:|
| 0.10 | 2452 Hz | 2754 Hz | 2641 Hz |
| 0.15 | 2505 Hz | 2780 Hz | 2749 Hz |
| 0.20 | 2571 Hz | 2888 Hz | 2848 Hz |
| 0.25 | 2653 Hz | 2995 Hz | 2962 Hz |
| 0.35 | 2801 Hz | 3202 Hz | 3178 Hz |

Every roll-off meets 110C's 3 kHz. Only 0.15 and below meet 2.8 kHz at -26 dB, and with the
default filter length (8 symbols each side) 0.15 does so by 20 Hz; doubling the length
gives 51 Hz, because the -26 dB points of a short filter are set partly by its truncated
skirts. 0.35 is for use only where wider emissions are authorised. These are audio
measurements: a real transmitter's distortion widens them, so the on-air spectrum must
still be checked.

### What it costs the receiver

`python tools/rolloff_check.py ber`: twelve 20-s messages per point, through the complete
receiver; noise only 8 and 11 dB below each mode's Table XVI point, and its Table XVI fading
channel at and 3 dB below the point. Transmit roll-off 0.15, 0.25 and 0.35, the receiver's
filter at 0.25 or matched. Messages received without error, of 12:

| Mode, channel, SNR | TX 0.15, RX 0.15 | 0.15, 0.25 | 0.25, 0.25 | 0.35, 0.25 | 0.35, 0.35 |
|---|---:|---:|---:|---:|---:|
| 2400S, noise, 10 dB | 12 | 12 | 12 | 12 | 12 |
| 2400S, fading, 18 dB | 4 | 4 | 5 | 6 | 6 |
| 1200S, noise, 3 dB | 6 | 6 | 6 | 6 | 6 |
| 1200S, fading, 11 dB | 3 | 6 | 3 | 5 | 5 |
| 600S, noise, -1 dB | 4 | 3 | 4 | 3 | 7 |
| 600S, fading, 7 dB | 5 | 5 | 5 | 4 | 5 |
| 75S, fading, 2 dB | 11 | 11 | 11 | 11 | 11 |

Bit error rates over the same messages:

| Mode, channel, SNR | 0.15, 0.15 | 0.15, 0.25 | 0.25, 0.25 | 0.35, 0.25 | 0.35, 0.35 |
|---|---:|---:|---:|---:|---:|
| 2400S, noise, 7 dB | 3.7e-4 | 4.1e-4 | 3.1e-4 | 3.6e-4 | 3.6e-4 |
| 2400S, fading, 18 dB | 1.2e-3 | 1.3e-3 | 1.1e-3 | 7.1e-4 | 5.9e-4 |
| 1200S, noise, 3 dB | 3.3e-4 | 2.4e-4 | 3.1e-4 | 2.7e-4 | 2.9e-4 |
| 1200S, fading, 11 dB | 4.3e-3 | 4.4e-3 | 4.3e-3 | 4.1e-3 | 4.2e-3 |
| 600S, noise, -1 dB | 5.3e-4 | 6.7e-4 | 6.5e-4 | 7.4e-4 | 5.7e-4 |

Within what twelve messages can resolve, the roll-off makes no difference to this
receiver, from 0.15 to 0.35, matched or not: the equaliser absorbs the small change in
pulse shape. (Points where nearly nothing decodes, and 75 bps, whose rates are dominated by
the odd lost message, are in the tool's output but say nothing either way. 2400S on the
fading channel leans towards the wider pulses, by about one message in twelve, which is
inside the spread.) So the amateur limit costs nothing measurable here; how other 110
modems receive a 0.15 signal is a question for interoperability testing (PLAN.md S9).

**Defaults changed (2026-10-20).** From here on the transmitter defaults to roll-off 0.15 with
its filter spanning 16 symbols each side, and the receiver's filters to 0.15 (INT-015;
docs/NETWORK.md, D5 and D7). Every measurement above this section, and CONFORMANCE.md, was
made with the earlier default, 0.25 with span 8; the comparison above says the difference
does not matter to this receiver, but the tools now rerun at the new default.

## P1: gaps in the input, from a sound card that drops samples (2026-10-20)

A program reading a sound card can fall behind, and the card then drops input. The modem
program (PLAN.md P1) was measured on what that does to a message in progress: 8 s messages,
noise only, one 0.1-s block of input lost 4 s in. Bytes wrong (of all sent), and how the
message ended:

| Mode | Block lost (a jump in time) | Block repeated | Block replaced by zeros |
|---|---|---|---|
| 2400S | 1500 of 2400; signal lost | 1500 of 2400; signal lost | 0 |
| 1200S | 750 of 1200; signal lost | 750 of 1200; signal lost | 0 |
| 600L | 0; lost, then a second message from a later preamble segment | the same | 0 |
| 75S | 47 of 75; signal lost | 47 of 75; signal lost | 0 |

A jump in time (or a block delivered twice) moves every later symbol a tenth of a second
away from where the demodulator expects it, and it never finds them again. Zeros in place
of the lost samples keep the time base: the receiver sees a short fade. So the station's
input buffer (`audio.InputRing`) replaces what it drops with zeros, and counts it (a
`Fault` event, "overrun"). A gap reported by the sound card's driver without a count cannot
be repaired this way; a dropped USB transfer from an SDR can be, if its driver says how
much.

### 2400 bps turned its data upside down after a gap

With zeros, most gaps cost nothing: 1200S, 600S, 150S, 75S and long interleave came through
gaps of 0.02-0.5 s with at most the bytes under the gap damaged (1200S and 600S lost a few
dozen bytes to 0.5 s; 2400L, 75L nothing). But 2400S lost the rest of the message after a
gap of 20-50 ms at every one of six positions tried, and of 100 ms at three of six (10 ms
never): about 900-1070 of 2400
bytes, with the end-of-message found and the right length.

The trace showed why. The bits after the gap were about 90% wrong: *inverted*, which the
convolutional code passes straight through (both generators have odd weight), so the EOM
was still found once it ended. In the data phase each frame's channel estimate is fitted to
the frame's decided data and its probe together, 32 decided symbols to 16 known at 2400S.
The frame straddling the gap gave an estimate turned by 180 degrees; the next frame's
tentative decisions, made with it, came out turned by 180 degrees too, agreed with it, and
outvoted the probe: the same again, frame after frame. The fit showed it (a share of signal
of about 0.2, against 0.999 before the gap), and so did the carrier loop, which took the
jump for half a hertz of frequency. The lock lasted until that frequency error drifted it
free, about 3 s later.

**The fix: the probe has the last word on the phase** (`EqualizingDemodulator._anchor_phase`).
When a data frame's fit is poor (below 0.5), its probe alone is correlated with the
received samples; if that says the estimate is turned by 90 degrees or more (a multiple of
45: the ambiguity 8-PSK decisions can fall into) and correlates well (at least 0.5), the
estimate is turned back before the frame's decisions are committed. The message notes how
often it happened. Measured (twelve 20-s messages each, the same channels before and after):

| Channel | Before | After |
|---|---|---|
| 2400S, noise, a 50-ms gap at twelve positions | 36,436 bits wrong in 6 messages | none |
| 2400S, 2 paths 2 ms 5 Hz, 25 dB | 421 bits wrong (11 messages) | the same |
| 2400L, 2 paths 2 ms 5 Hz, 30 dB | 5 (1 message) | the same |
| 1200S, 600S, 1200L and 2400S on 1-5 Hz fading | | the same |

Two other rules were tried and dropped: correcting turns of 45 degrees as well (on 2400S at
5 Hz, the probe in a deep fade was as often wrong as right: 610 bits instead of 421), and
guarding the carrier loop against jumps (the frequency stayed put, but the decisions stayed
inverted: the loop was a symptom, not the cause). The quick conformance run (`--quick`) was
unchanged but for the noise of one channel per row.

### Keeping up

The station, receiving through a virtual device (noise at 34 dB below the signal, one
2.6-GHz core, the full test suite running alongside): seconds of processing per second of
signal.

| 4800S | 2400S | 2400L | 1200S | 600S | 150S | 75S | 75L | noise only | noise, late entry on |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.19 | 0.23 | 0.18 | 0.24 | 0.25 | 0.20 | 0.09 | 0.08 | 0.06 | 0.27 |

A test holds it under 0.75 for three modes (`tests/unit/test_station.py`; set
`M110C_SKIP_TIMING` on a very slow machine). The transmitter keeps a second of output queued
ahead of the sound card, so a slow moment of the receiver's does not starve it.

One measurement pitfall worth keeping: a process pool reuses its workers, so a variant set
by changing module constants in one job stays set for the next jobs in that process. Every
job must set every constant it depends on.
