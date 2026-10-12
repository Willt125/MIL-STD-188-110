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
| DIGI-VX | (7,7)? | too weak to read reliably; inconclusive |
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
| DIGI-VX | nothing found | nothing found |

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

## Limitations

- The exploratory demodulator is not committed; the real receiver (`m110c-rx`, above) has
  superseded it. The recordings remain outside the repository, so these runs are manual
  acceptance checks, not CI tests.
- Only one recording (the fox) has known plaintext. For the others, "EOM found" proves that the
  chain inverts correctly, not that every bit is right. Readable text proves it for those files.
- Zero-interleave flush padding (INT-013, no interleaver) and 75 bps with zero interleave were
  not present in any recording.
