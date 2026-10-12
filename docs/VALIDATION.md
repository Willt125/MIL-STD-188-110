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
2. **Sample-clock drift is far larger than the spec's tolerance.** Several recordings drift by
   about +3500 ppm (0.35%). The spec's ±10 ppm (§4.2.1) is a transmitter requirement, but
   captures pass through sound cards and resamplers. **The RX timing loop (M8/M10) must track at
   least ±0.5%.** Without tracking, coherence collapses within about a second.
3. **Carrier offsets observed:** 1784–1810 Hz, i.e. within about ±20 Hz of 1800 Hz. The ±75 Hz
   acquisition target in the plan is comfortably wide.
4. **Probe-aided demodulation works.** Clean recordings decode with nothing more than per-frame
   gain and timing from the probes. That is a useful baseline to beat in M10.

## Limitations

- The exploratory demodulator is not committed. Its method is described above so the analysis
  can be redone; the real receiver (M7–M10) will supersede it, and the recordings that decoded
  here become its acceptance tests.
- Only one recording (the fox) has known plaintext. For the others, "EOM found" proves that the
  chain inverts correctly, not that every bit is right. Readable text proves it for those files.
- Zero-interleave flush padding (INT-013, no interleaver) and 75 bps with zero interleave were
  not present in any recording.
