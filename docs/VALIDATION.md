# External Validation Against Real Recordings

**Date:** 2026-10-12. **Code under test:** milestones M1–M5 (transmit chain), plus a throwaway
exploratory demodulator written for this analysis.

The spec publishes no test vectors, so until now every check in this repository compared our
code with our own reading of the spec. This report records the first comparison against signals
from real modems.

## Source

Nineteen recordings of MIL-STD-188-110A serial-tone transmissions from the Signal Identification
Wiki: <https://www.sigidwiki.com/wiki/MIL-STD-188-110_Serial>. The serial-tone waveform has not
changed between 110A and 110C, so 110A recordings are valid references. The recordings are
third-party material and are **not** included in this repository. Only small derived data is,
in `tests/vectors/irl_fox_2400S.json`.

One recording carries known plaintext: "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 0123456789".
The person who supplied the files notes that the 300S recording is a Thales Système 3000 variant.

## Method

The receiver (M7–M10) does not exist yet, so a deliberately simple exploratory demodulator was
written outside the package. It reuses the package's tables, scramblers, interleaver and Viterbi
decoder; that is the point, since those are what is being tested.

1. Audio was converted to mono float32 (ffmpeg), then to a complex analytic signal. The stereo
   WAVs turned out to be dual-mono, not I/Q.
2. **Preamble search:** the 288 known tribits at the start of every preamble segment (channel
   symbols `0 1 3 0 1 3 1 2 0`, expanded by Table XIII and scrambled by our sync sequence) are
   correlated against the signal over a grid of carrier frequencies.
3. **Preamble readout:** each 32-tribit slot is classified against the 8 Table XIII patterns.
   This yields D1, D2 and the three count symbols, which are then decoded by *both* the text rule
   and Table XII.
4. **Data phase:** matched-filter (RRC α = 0.25) samples at 16 samples/symbol. Data is
   descrambled with *our* data scrambler, and timing and carrier phase are tracked frame by frame
   from the probes. Bits are then soft-demapped (max-log) and pass through our deinterleaver,
   de-repetition and soft Viterbi decoder.
5. **75 bps:** each 32-tribit set is classified non-coherently against all 4 normal and
   4 exceptional patterns, so exceptional-set positions are *observed*, not assumed.
6. **Transmitter comparison:** the decoded fox payload is fed to *our* transmitter, and its output
   is compared symbol by symbol with the hard-decided symbols of the real transmission.

## Results by recording

EOM = an exact 32-bit match of 0x4B65A5B2 in the decoded stream. If the decoder produced
random bits, the chance of a spurious match is about N/2³² for an N-bit stream: 1 in 170,000 for
the longest stream here (1200L), and far less for the rest.

| Recording | Mode (from preamble) | Outcome |
|-----------|----------------------|---------|
| quick-brown-fox | 2400S | **560 characters, 0 errors**, EOM; our TX matches 7199/7200 data symbols |
| 150S / 150L | 150S / 150L | **fox text, 0 errors**, EOM |
| 1200L | 1200L | **plain text, 0 framing errors in 1697 chars** (`V V V DE BDA21 … RYRYRY`), EOM |
| 600L | 600L | plain text (`V V V DE BXT78 … RYRYRY`); recording ends before EOM |
| 1200S, 600S, 300S, 300L, 2400S, 2400L | as labelled | EOM found; payload is binary, not text |
| 75S | 75S | exceptional sets at 44, 89, 134…; EOM found with the MGD, not without |
| 75L | 75L | exceptional sets at 359, 719; EOM found |
| 4800 (no interleave) | 4800 | mostly readable text; residual errors expected (uncoded 8-PSK, no equalizer) |
| DIGI-VX | 2400V (7,7) | first read as too weak; in fact a 0.47% clock error. Corrected: the 2400S chain, EOM found (below, "Digital voice") |
| Long multi-burst files (ARQ, ALE, voice, ryryry) | — | not analysed yet; candidates for RX acceptance tests |

Every one of the 13 data-mode D1/D2 pairs in Table XI was identified in at least one preamble.

## What this settled

See the status table in [`SPEC_NOTES.md`](SPEC_NOTES.md). Highlights:

- **Table XII is an erratum (INT-001).** Real preambles count 23, 22, … 0 by the text rule.
  Read through Table XII as printed, the same symbols give 43, 41, 42, 40…
- **Figure 6 and Figure 4 readings (INT-003, INT-002).** Both could only be checked externally,
  because each alternative reading (mirrored scrambler taps, time-reversed encoder register)
  decodes its own output perfectly. The real modem matches ours and rejects both alternatives
  (`tests/unit/test_irl_fox.py`).
- **Our transmitter is symbol-exact with a fielded modem at 2400S,** including transmission
  length: the real signal ends after exactly the 150 frames our framer emits.

## Findings that change plans

1. **Real modems carried asynchronous characters.** Every decoded text payload is an 8N1 UART
   stream (start bit, 8 data bits LSB first, stop bit; idle = 1s), sent bit for bit over the air.
   Our transmitter sends raw bytes as a synchronous bit stream (INT-016). That is valid, but a
   real modem's DTE receiving our signal would see unframed bits. **Proposal:** an `--async 8N1`
   framing option on `m110c-tx` and `m110c-rx` (M6/M12), off by default.
2. **Sample-clock drift exceeds the spec's tolerance in some captures.** Measured timing drift
   (linear fit to the tracked timing over locked frames):

   | Recordings | Drift |
   |------------|-------|
   | 2400S, 2400L, 1200S, 1200L (one sigidwiki set) | +885 to +938 ppm (≈ 0.09%) |
   | fox, 4800, 600S/L, 300S/L, 150S/L, RadioScanner f1 | 0 to +35 ppm |

   The spec's ±10 ppm (§4.2.1) is a transmitter requirement; the large values are almost
   certainly the capture chain (sound card or resampling), since the other recordings are
   clean. **The RX timing loop (M8/M10) should track at least ±0.2%.** Without tracking,
   coherence collapses within about a second in the drifting set.
   *Correction:* the first version of this report gave "+3500 ppm (0.35%)" and "±0.5%". That
   was a units error in the analysis script (samples per symbol hard-coded as 4 in a 16-sample
   run). The figures above replace it.
3. **Carrier offsets observed:** 1784–1810 Hz, i.e. within about ±20 Hz of 1800 Hz. Wavecom's
   commercial decoder allows ±400 Hz of adjustment (see below), so real-world mistuning can be
   much larger than in these clips. **Proposal:** widen the M8 acquisition target from ±75 Hz
   to ±400 Hz.
4. **Probe-aided demodulation works.** Clean recordings decode with nothing more than per-frame
   gain and timing from the probes. That is a useful baseline to beat in M10.

## Second batch (2026-10-12): RadioScanner.ru and Wavecom

**RadioScanner.ru recording `f1.wav`** (8 kHz mono; their measured baud rate 2400.04 Bd):
- A very clean 2400L transmission (probe coherence median 1.00, drift +3 ppm) that starts
  mid-preamble. Its counts read **11, 10 … 0 by the text rule** (Table XII as printed gives 7, 5, 6,
  4…): independent confirmation of INT-001 from a second source.
- Our scrambler, probe layout and decoder run cleanly through 1108 frames. No EOM appears. The
  payload is a short binary header followed by zeros, and the clip ends about 23 frames after
  the last locked frame, so the message most likely continues past the end of the clip.
- Their sonogram, autocorrelation (200 ms period, one preamble segment) and constellation
  images agree with the spec and with our analysis.

**Wavecom's product page for its MIL-188-110A decoder** (a screenshot was supplied). This is
secondary documentation from a commercial decoder vendor, not a measurement:
- User data "is generally transmitted in binary mode, i.e., in a transparent mode". The decoder
  offers **ASCII SYNC (each 8 bits LSB first)**, which is our default (INT-016), and **ASCII
  ASYNC (start bit 0, 8 data bits LSB first, at least one stop bit 1)**, the framing found in
  the sigidwiki recordings.
- A **Polarity** setting, NOR = USB and INV = LSB, confirms that sideband inversion (INT-021)
  needs handling in practice.
- Centre frequency adjustable by **±400 Hz**, and the decoder "synchronises both on the preamble
  and on the probe sequence", i.e. late entry without a preamble (our stretch goal S5).
- Its mode table matches ours, including 4800 bps as "ZERO (No interleaver)" (INT-022).

**Transmission length across modes (supports INT-013).** The number of frames over which our
tracking stays locked is an exact whole number of interleaver blocks in 7 recordings: 1200S
(17 blocks), 1200L (5), 2400L (1), 600S (7), 600L (4), 300S (9), 300L (1). The 150 bps
recordings and the short 2400S clip do not divide evenly. They decode correctly, so this is
more likely imprecision in the locked-frame count near the end of the signal than a real
deviation. This is supporting evidence, not proof.

## The M8 receiver on the same recordings (2026-10-12)

The exploratory demodulator above was replaced by the real receiver, `m110c-rx`. It is fully
automatic: no genie, no hand-tuned parameters. Recordings converted to 9600-Hz mono WAV with
ffmpeg, then `m110c-rx -f wav --async 8N1`:

| Recording | Result |
|-----------|--------|
| fox (2400S) | 560 characters, perfect, EOM |
| 150S, 150L | fox text, perfect, EOM |
| 1200L, 600L | plain text (`V V V DE BDA21 …`, `V V V DE BXT78 …`), EOM. The exploratory run never found 600L's EOM |
| 2400L, 2400S, 1200S, 600S, 300S, 300L, 75S, 75L | EOM found; binary payloads as before |
| 4800 | EOM found; text corrupts after ~25 characters (uncoded 8-PSK on a real channel needs the M10 equaliser; M10 decodes it, below) |
| RadioScanner f1 (2400L, clip starts mid-preamble) | tracked to the end of the clip; "signal lost, no EOM" (the clip is cut off). Since M9 the partial last block is decoded with erasures: 3844 bytes instead of 3087 |

Measured carrier offsets were −16 to +11 Hz, and transmitter clock offsets up to about ±1400 ppm
were tracked automatically. The reported SNRs (0.7–20 dB) are pessimistic for clips this
clean; that is listed for M10.

## The M10 receiver on the same recordings (2026-10-12)

Same files and command; the equaliser is now the default (`--no-equalizer` gives the M8 result
for comparison).

| Recording | M8 | M10 |
|-----------|----|-----|
| **4800 (no interleave)** | text corrupt after ~25 characters | **the fox text, repeated, readable throughout**, EOM |
| ARQ bursts (2400L) | garbled (`.EQT.PC.E1.XQT…`) | clean repeated `EQT` blocks, EOM |
| fox, 150S/L, 1200L, 600L, the ALE/ryryry files | text, EOM | the same text, EOM |
| 2400S/L, 1200S, 600S, 300S/L, 75S/L, RadioScanner f1 | EOM (binary payloads) | the same |
| DIGI-VX | nothing found | nothing found (clock error; see "Digital voice") |

Two of the M8 open points are explained by this:

- **Reported SNR was pessimistic** because the decoder also measured the probes of frames
  after the signal had ended (noise), which the M8 tracker passed on before declaring loss.
  The M10 demodulator judges loss over a second and withholds the bad units at the end, so the
  report covers the signal: e.g. 150S 10.4 → 29.2 dB, 300L 0.7 → 13.0 dB, 2400L 5.4 → 10.9 dB.
- **Clock-offset reports** now agree between recordings that share a capture chain: 2400S,
  2400L and 75L give −876 to −887 ppm (the +885–938 ppm drift found above, with the sign
  convention of `m110c-rx`), where M8 gave −838 to −1169. M8 reported +1420 ppm for 75S;
  M10 reports −43.

Processing took 0.1–0.4 s per second of recording (M8: 0.05–0.15 s).

## Independent channel simulators (2026-10-12)

Our fading-channel results (PERFORMANCE.md) were all measured on our own Watterson simulator
(M9). It passes the Appendix E.7 checks, but a simulator and a receiver written by the same
hands can share a mistake that flatters both. So two independently written HF channel
simulators were tested against the same E.7 checks, and the one that passed was used to
re-run the receiver measurements. No code from either is in this repository.

### N4HY HFPathSimulatorLiveData: not usable as a reference

[n4hy/HFPathSimulatorLiveData](https://github.com/n4hy/HFPathSimulatorLiveData) (MIT, 2026)
is a large real-time simulator (Watterson, Vogler–Hoffmeyer, ITU presets, SDR I/O). Only its
pure-Python Watterson and noise code could be tested: the compiled path needs CUDA. Measured:

| Check | Result |
|-------|--------|
| Rayleigh statistics, path independence, delays | pass |
| Doppler spectrum (Table E-I) at the nominal setting | fail: its "spread" gives a spectrum σ of spread/√2, Appendix E's is d/2; dividing the setting by √2 passes at 0.5 and 1 Hz |
| Doppler spectrum at 5 and 10 Hz | fail whatever the setting: σ is capped at 2.26 Hz (fading filter clamped to one 50-ms update) |
| Fading rate | depends on the caller's block size (one update per call): a "1 Hz" channel measured σ 0.09 Hz in 4096-sample calls, 2.1 Hz in 160-sample calls |
| Zero Doppler | not a fixed path: the gain is redrawn at random every 50 ms |
| Noise | SNR over the full sample-rate band, not 3 kHz (10 dB set = 15 dB in 3 kHz at 9600 S/s), and rescaled to each block's signal power, so the noise falls with every fade |

Its CI has also not passed anywhere in the commit history we looked at. Not used further.

### PathSim 1.0 (AE4JY): agrees with Appendix E and with our results

PathSim (Moe Wheatley AE4JY, 2000, GPL v2+) is the long-standing amateur HF path simulator:
Windows, 8000 S/s, up to three paths. Its channel code (`CPath`, `CGaussFIR`, `CDelay`,
`CNoiseGen`) was ported to NumPy for measurement; the original was not run. Because that port
is a translation of GPL code it is not committed; the method is below so it can be repeated.

Its design matches Appendix E closely: the spread is the 2σ bandwidth (fading-filter power
spectrum e^(−2f²/d²), as E.5.4), gains are computed at 12.8, 64 or 320 S/s (exactly 32× the
spread at each range's upper edge) and brought to 8000 S/s by ×5 interpolation stages, and the
fading filter is truncated at about ±3.1σ (≈1% of its peak).

| E.7 check on the port | Result |
|-----------------------|--------|
| Doppler spectrum, Table E-I, at 0.5, 1, 2, 5, 10 Hz | pass at all five; σ within 2% of d/2; worst point 1.9 dB (0.5 Hz, 30 dB down; 2.0 allowed) |
| Rayleigh statistics | P(\|h\|² < 0.1 mean) 0.098 (ideal 0.095); MSVC's 15-bit `rand()` makes no measurable difference |
| Continuity | no steps (largest sample-to-sample change 4.6× the median) |
| Paths | equal power (1/n), independent (correlation 0.008); delays whole samples at 8000 S/s |
| Noise | 3-kHz band-limited, S/N 10.04 dB for 10 set; level fixed while the signal fades |

One caveat for anyone using PathSim itself: the noise level follows a running average of the
*input* level (about 5 s), starting from a fixed value, so the SNR is wrong for the first
seconds of a run and around silences, which is where a preamble usually is.

**Cross-check.** Our simulator was run with only its fading process replaced by the port's
(generated at 8000 S/s, interpolated to 9600; the gains are band-limited to tens of hertz, so
this is exact for practical purposes). Delays, the analytic conversion and the Appendix E
noise stayed ours, so differences come from the fading alone. The same scripts and seeds as
PERFORMANCE.md (M11):

| Measurement | Our fading | PathSim fading |
|-------------|------------|----------------|
| 75L on 5 ms, 5 Hz, clean of 16 at 2 / 0 / −1 dB | 16 / 15 / 8 | 16 / 16 / 9 |
| Table XVI fading rows, 4 channels each | all meet the requirement | all meet the requirement (4800 bps on 2 ms, 0.5 Hz: BER 6e-4 from two channels, against 1e-3) |
| Short interleave, decoded of 20, per fading row | 16–20 | 18–20 |
| 2400S on 2 ms, 5 Hz, decoded of 20 | 17 | 19 |
| First-segment detections, 100 channels: 75S at 2 dB / 300S at 7 dB | 78 / 87 | 69 / 84 |

Every difference is within the run-to-run spread. One pattern in 20-channel runs (more
first-segment detections with PathSim's fading on the 5-Hz rows) reversed with 100 channels.
At −1 dB the 75L failures look the same with either fading: partial decodes of a few hundred
bit errors, the demodulator's limit.

**What this shows, and what it does not.** It rules out an implementation error in our fading
process that flatters the receiver. It cannot rule out a misreading of Appendix E itself that
PathSim shares: both descend from the same Watterson/CCIR tradition. The real recordings
remain the check against reality.

**Repeating it:** port PathSim's `CPath::CalcPath` gain generation (Gaussian FIR at the
spread-dependent rate, then the ×5 `X5IntrpFIRCoef` stages), and substitute it for
`m110c.channel.watterson.FadingGain` (same `next(n)` interface; per-path power as passed).

## Digital voice (2026-10-13)

`MIL188-110A_DIGI-VX` is a 6-s clip, originally an MP3, of a 2400 bps digital-voice
transmission. It had been set aside as "too weak". It is not weak: it runs **0.47% slow**.
Its strongest preamble peaks are 1929 samples apart instead of 1920, so across one 0.2-s
segment the slots drift by two symbols and the last ones (the count) read wrong. Resampled by
1920/1929, the preamble reads perfectly: three segments, D1/D2 = 7,7, counts 2, 1, 0, carrier
offset +5.6 Hz. That error is far beyond any working sound card; it is presumably a
sample-rate conversion in the clip's history, and the acceptance test undoes it.

Decoded as 2400S with the voice designators (probe SNR 9.1 dB):

| Check | Result | Control |
|-------|--------|---------|
| D probes (last two of each block) | match 7,7: error 0.08 | 6,4: 2.03; 4,4: 2.03; 7,6: 0.96. The fox recording matches 6,4 (0.005) |
| Re-encode the Viterbi output, compare with channel decisions | **99.9%** agree with the 2400S interleaver | no interleaver 84.3%; random soft bits 82.8% (a K=7 Viterbi decoder always finds a nearby path) |
| EOM | exact match after 9056 payload bits, then the flush | |

So the voice transmission is the 2400S data chain, D probes included, and it ends like a data
message (INT-019). The payload: about 0.2 s of a repeating header, about 3.4 s of bits with
49% ones and 50% transitions, then the EOM. Tested against STANAG 4198 Annex C (54-bit
frames; bit 54 alternates 1, 0; the 7-bit pitch word has Hamming weight 0, 3, 4 or 7), at all
54 offsets: best sync alternation 0.62 and pitch-word validity 0.62, against 0.59 and 0.63 for
random bits; a synthetic 4198 stream scores 1.00 and 1.00. No column alternates at any period
from 20 to 200 bits either, which also argues against MELPe (which has a sync bit). The
payload is consistent with encrypted voice, so this recording cannot identify the codec.

## As acceptance tests (M12, 2026-10-13)

The results above are now checked by `tests/acceptance/test_recordings.py`: exact text for
the fox, 150S/L and plain-text files, and mode, byte count and EOM for the binary payloads,
in 8N1 framing; for the digital-voice clip, ignored by default and, with voice enabled and
its clock corrected, 2400V with 1132 bytes and the EOM. The tests run only when `M110C_RECORDINGS` names a directory holding the
recordings as 9600-S/s WAV files under their original names; the recordings themselves stay
outside the repository.


- The exploratory demodulator is not committed; the real receiver (`m110c-rx`, above) has
  superseded it. The recordings remain outside the repository, so the acceptance tests run
  only where they are available locally, not in CI.
- Only one recording (the fox) has known plaintext. For the others, "EOM found" proves that the
  chain inverts correctly, not that every bit is right. Readable text proves it for those files.
- Zero-interleave flush padding (INT-013, no interleaver) and 75 bps with zero interleave were
  not present in any recording.
