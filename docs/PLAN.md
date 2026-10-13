# Implementation Plan: MIL-STD-188-110C Serial-Tone Modem

Status: **accepted** (2026-10-11). Decisions recorded as **[DECIDED]**.

---

## 1. Goals and non-goals

### Goals (definition of done)

1. **Encoder (`m110c-tx`)**: arbitrary bytes from a file or stdin go in. A baseband I/Q stream
   comes out, to a file or stdout. Every fixed-frequency data mode in §5.3.2 / Table XV is supported:

   | Rate (bps) | Interleave settings     | Notes                                   |
   |-----------:|-------------------------|-----------------------------------------|
   | 4800       | (none: bypassed)        | uncoded, DO in the spec                 |
   | 2400       | zero, short, long       | data only (2400 digital voice excluded) |
   | 1200       | zero, short, long       |                                         |
   | 600        | zero, short, long       |                                         |
   | 300        | zero, short, long       | rate-1/2 code repeated 2x               |
   | 150        | zero, short, long       | rate-1/2 code repeated 4x               |
   | 75         | zero, short, long       | 32-symbol orthogonal sets, no probes    |

2. **Decoder (`m110c-rx`)**: a baseband I/Q stream goes in, from a file or stdin. The original
   bytes come out, to a file or stdout. The decoder detects the mode from the preamble (D1/D2)
   automatically. The one exception is zero vs. short interleave, which the preamble cannot tell
   apart (see SPEC_NOTES INT-012).
3. **Pipe-able**: both tools run as streaming filters with bounded memory. The memory bound is
   independent of message length and dominated by one long-interleaver block.
4. **Readable and academic**: the code is organized to follow Figure 2 of the spec block-by-block.
   Every spec-derived constant cites its paragraph or table. Every interpretation of an ambiguous
   passage is recorded in [`SPEC_NOTES.md`](SPEC_NOTES.md) and referenced from code and tests.
5. **Heavily tested**: each functional block ships with unit tests, independent oracles, and
   round-trip/property tests before the next block builds on it (see §4).

### Non-goals (for v1.0)

- Frequency-hopping operation (§5.3.3, defers to the separately-controlled MIL-STD-188-148).
- 2400 bps digital voice (D1/D2 = 7,7).
- Appendix A (TCP/UDP modem control), RTS/CTS/radio keying, remote control (§5.3.1.5).
- Real-time sound-card I/O. We read and write sample streams; the user can pipe to `aplay`,
  `sox`, GNU Radio, an SDR, and so on.
- The optional appendices (B: 39-tone, C: 3200–9600 bps, D: wideband, F: multi-channel). They
  are listed as stretch milestones in §6. **[DECIDED]** v1.0 covers the §5.3.2 serial-tone
  section only; Appendix C is a stretch goal.

---

## 2. Technology choices

### 2.1 Language **[DECIDED: Python 3.11+ / NumPy]**

**Recommendation: Python 3.11+ with NumPy as the only runtime dependency.** pytest and
hypothesis are used for tests; matplotlib is optional, for inspection notebooks only.

- *Readability and academic use*: the code reads close to the spec's own block diagram, and
  DSP students and researchers can run, plot and modify it directly.
- *Performance is not a real constraint at these rates*: 2400 symbols/s, at most 4800 coded
  bits/s, and a 64-state Viterbi decoder vectorized across states. A NumPy implementation should
  run several times faster than real time on a laptop. We will measure this in M6/M11 rather than
  assume it.
- *"Leaks" in Python* means unbounded buffer growth, state carried across messages, and
  unclosed file descriptors. These are tested explicitly in §4.4.

**Alternative: C99** with no dependencies, tested under ASan, UBSan and Valgrind, and fuzzed with
libFuzzer. Choose C if embeddability or literal memory-leak checking matters more than
approachability. It roughly doubles the effort per milestone. A middle path is to write the
Python reference first and port the frozen design to C as a stretch goal (§6), using the Python
golden vectors as the C test oracle.

### 2.2 Sample-stream format defaults **[DECIDED: as proposed]**

| Parameter    | Proposed default | Options |
|--------------|------------------|---------|
| Sample type  | `cf32`: interleaved little-endian float32 I/Q (GNU Radio / SDR convention) | `cs16`, real `s16` audio, `.wav` (real audio, 1800 Hz carrier) |
| Sample rate  | 9600 S/s (exactly 4 samples/symbol) | 48000 (20 sps); arbitrary rates via a rational resampler (later) |
| Carrier      | +1800 Hz in the complex stream, so `Re{x}` *is* the §5.3.2.3.9 audio waveform | `--carrier 0` for symbol-centered baseband |
| Pulse shape  | Root-raised-cosine, α = 0.25, span ±8 symbols | configurable α. The spec does not specify a filter (§5.3.2.3.9 note, INT-015). α = 0.25 occupies exactly 300–3300 Hz around 1800 Hz |
| Byte→bit order | LSB-first, as on a UART | `--msb-first` (the spec is silent, INT-016) |

---

## 3. Architecture

### 3.1 Repository layout

```
pyproject.toml
src/m110c/
  spec/            # transcribed tables and constants ONLY, each with a § citation
    tables.py      #   Tables VI, VII, VIII, IX, X, XI, XII, XIII, XV; EOM; sync scramble seq
    modes.py       #   ModeSpec dataclass: every derived quantity computed from the tables
  bits.py          # bytes <-> bits, bit-order conventions
  fec.py           # K=7 convolutional encoder, repetition, soft-decision Viterbi
  interleave.py    # matrix load/fetch (§5.3.2.3.4/5) and its inverse
  mgd.py           # modified-Gray "decoder" (TX mapping) and soft inverse
  scrambler.py     # data LFSR (Fig. 6) and the 32-symbol sync sequence
  symbols.py       # symbol formation: data, probes (incl. D1/D2), 75-bps sets, preamble
  tx.py            # transmit framer: phases a-d of §5.3.2.1 -> tribit stream
  modulate.py      # tribits -> 8PSK -> pulse shaping -> carrier -> samples
  rx/
    frontend.py    # downconvert, matched filter, decimate
    acquire.py     # preamble detection, coarse freq/timing, D1/D2/count decode
    track.py       # timing and carrier tracking
    equalize.py    # probe-trained adaptive equalizer (LMS/RLS DFE)
    demap.py       # soft demapping, descrambling, 75-bps set correlation
    deframe.py     # deinterleave, de-repeat, Viterbi, EOM detection
  channel/         # test infrastructure, also shipped as a CLI
    awgn.py
    watterson.py   # Appendix E simulator
  io.py            # sample formats, chunked readers/writers
  cli.py           # m110c-tx, m110c-rx, m110c-chan
tests/
  unit/            # one file per module
  oracle/          # slow, literal "read the spec aloud" implementations
  vectors/         # committed golden vectors (+ generator script)
  loopback/        # TX -> channel -> RX, all modes
  perf/            # Table XVI conformance (slow, opt-in marker)
docs/
  PLAN.md          # this file
  SPEC_NOTES.md    # interpretation register + spec errata
  walkthrough/     # (later) notebooks following one bit through the modem
```

### 3.2 Design rules

- **One module per Figure 2 block.** Each module has pure functions (or small classes with
  explicit `state`) and no hidden globals. TX and RX for a block live together so their tests
  can check inverses.
- **Streaming through explicit state, not generators-of-generators.** Every stage is an object
  with a `process(chunk) -> chunk` method and a `flush()`. This keeps the data flow inspectable
  and makes "same output regardless of chunking" testable.
- **Derived, not hard-coded.** Block sizes, frames per block, preamble lengths and probe
  periods are computed from the transcribed tables. Unit tests then check that the derivations
  agree with the spec's own stated numbers (1440/11520 symbols, 0.6 s/4.8 s, and so on). If
  the tables are mis-transcribed, these cross-checks fail.
- **Interpretation IDs.** Any code that resolves an ambiguity carries a comment
  `# INT-nnn` pointing to SPEC_NOTES.md. Its test is named for the same ID.

---

## 4. Testing strategy

There is no reference implementation, so the tests are the main defense against
misunderstandings. Four layers:

### 4.1 Spec-anchored examples
The spec contains a handful of worked examples. Each becomes a test verbatim:
- EOM first eight bits are `0100 1011` (§5.3.2.3.1).
- Interleaver fetch at 2400/long: bit 2 from (row 1, col 559), bit 3 from (row 2, col 542) (§5.3.2.3.5).
- Count 23 → C1,C2,C3 = 5,5,7 (§5.3.2.3.7.2.1 note).
- Data scrambler has period 160 and reloads `0xBAD` (§5.3.2.3.8.1).
- Sync scrambler: 32 symbols, starts with 7 and ends with 6 (§5.3.2.3.8.2).
- Arithmetic identities: rows × cols = coded rate × interleave time; blocks are 1440 or 11520
  symbols; preamble segments are 480 symbols = 200 ms.

### 4.2 Independent oracles ("implement it twice")
Every spec-critical block gets a second implementation in `tests/oracle/`. It is written
literally from the prose: loops, row/column counters, no vectorization, no shared code. The
production version must match it exactly over randomized inputs:
- Interleaver: the oracle walks the "row += 9 mod 40" text. Production uses a precomputed
  permutation.
- Data scrambler: the oracle is a bit-by-bit Galois register drawn from Figure 6. Production
  uses a precomputed 160-entry table.
- Convolutional encoder: the oracle shifts bits through the seven named cells of Figure 4,
  one bit at a time. Production convolves the input with tap vectors derived from the
  polynomials. (This swaps the roles first proposed here: a literal figure transcription
  makes the better oracle.)
- Viterbi: the oracle is a brute-force maximum-likelihood search over all inputs for short
  blocks (up to about 14 bits). Production soft Viterbi must find the same codeword.

### 4.3 Properties and round trips (hypothesis)
- deinterleave(interleave(x)) == x, and the permutation is a bijection, for every mode.
- Viterbi(encode(x)) == x with no noise. It also corrects every error pattern of weight ≤ 4
  (d_free = 10 for this code; verified by a test that computes d_free).
- Soft demap: the LLR sign agrees with the hard decision; on 8PSK the LLR magnitude grows with SNR.
- TX symbol-stream invariants: correct preamble length, correct probe positions, D1/D2 only in
  the last two probes of each block, the scrambler reset every 160 symbols, and a total length
  that is an integer number of frames.
- Full loopback on a noiseless channel is bit-exact for all 19 mode/interleave combinations,
  with random message lengths including 0, 1 and block-boundary ±1 bytes.

### 4.4 Streaming, resources and robustness
- **Chunk invariance**: TX and RX output is byte-identical whether input arrives in one piece,
  1 byte at a time, or random chunk sizes. This is the core pipe-correctness test.
- **Bounded memory**: run a stream of ≥ 10 minutes of samples through RX and TX under
  `tracemalloc` and assert that peak memory stays flat after the first long block.
- **State isolation**: two encoders interleaved in one process produce the same output as two
  run separately. Back-to-back messages in one RX stream decode independently.
- **Garbage tolerance**: noise-only input, truncated transmissions, and random bytes as `cf32`.
  RX must not crash or hang, and must produce no output (or report a clean error). Use
  hypothesis-driven fuzzing on the RX entry point.
- **CLI tests**: subprocess pipelines (`m110c-tx | m110c-chan | m110c-rx`) with exit codes and
  stderr diagnostics checked.

### 4.5 Performance conformance (slow, opt-in)
- AWGN BER curves for 4800 (uncoded 8PSK) are compared against the closed-form result. The
  coded modes are compared against the known K=7 union-bound region.
- The Watterson simulator is validated against Appendix E.7 first (bandwidth, SNR on fixed and
  fading paths, Doppler spectrum shape). Then Table XVI is checked row by row. The output is a
  generated conformance report (a markdown table) committed with each release.

### 4.6 External validation (done for TX, 2026-10-12; see [`VALIDATION.md`](VALIDATION.md))
The serial-tone waveform has not changed since 188-110A, so any real-world recording of a
110A/B/C serial-tone transmission is a valid test input. Public signal-identification archives
host such samples. Decoding one successfully, and getting a sensible D1/D2 and EOM, is the
strongest available evidence that our interpretations are right. We use recordings only as
test input, never as a source of code.

### 4.7 Testing the tests
A green suite proves nothing if the tests cannot fail. Each milestone ends with a manual
mutation pass: plant plausible mistakes (typos, swapped rows/columns, off-by-one steps) in the
new module, and confirm that a test fails for each one. In M1 this pass found four tables that
nothing cross-checked: Table VI 75S rows/columns, the sync scrambler sequence, the EOM, and the
LFSR taps. That led to the "double entry" tests and the separately-transcribed row moduli.
Note: run mutants with `PYTHONDONTWRITEBYTECODE=1`. A same-size edit made within the same
second can otherwise be masked by a stale `.pyc`.

### 4.8 Receiver design inputs from real recordings
Measured in [`VALIDATION.md`](VALIDATION.md); M7–M12 must meet these, not only the spec's figures:
- **Timing drift: track at least ±0.2%** (up to +940 ppm observed in one set of captures; the
  spec's ±10 ppm is a transmitter limit).
- **Carrier offset: acquire over ±400 Hz** **[DECIDED 2026-10-12]**. The clips are within
  ±20 Hz, but Wavecom's decoder allows ±400 Hz for mistuned receivers.
- **Sideband inversion** (USB/LSB) occurs in practice: provide `--invert-spectrum` (INT-021).
- **Late entry** (sync on probes without a preamble) is offered by commercial decoders; it stays
  a stretch goal (S4/S5).
- **Async payloads** (8N1) are what real modems carried. `--async FORMAT` framing, per Appendix A
  send-all mode **[DECIDED 2026-10-12]**: framing in `framing.py` (done), TX CLI in M6, RX CLI in M12.
- The recordings that decoded in the exploratory analysis become RX acceptance tests (M12),
  run only where the recordings are available locally. **Done:** `tests/acceptance/`, run
  when `M110C_RECORDINGS` names a directory of the recordings at 9600 S/s.

---

## 5. Milestones

Each milestone ends with: all tests green in CI, coverage ≥ 90% of the new module, SPEC_NOTES
updated, and a short "what we learned" note in the PR description. Milestones up to M6 can be
reviewed purely against the spec. M7 onward is DSP work with judgment calls.

| # | Milestone | Deliverable | Key tests |
|---|-----------|-------------|-----------|
| **M0** | Scaffolding | `pyproject`, ruff, pytest, hypothesis, GitHub Actions CI, SPEC_NOTES seeded | CI runs; lint gate |
| **M1** | Spec tables + `ModeSpec` | `spec/tables.py`, `spec/modes.py` for all 19 modes | §4.1 arithmetic identities; transcription double-check (each table typed twice independently and diffed, or re-extracted from the PDF text) |
| **M2** | Bits + FEC | `bits.py`, `fec.py`: encoder, repetition, **soft** Viterbi | impulse response = generators; d_free = 10; brute-force ML oracle; error-correction properties; repetition pair ordering (§5.3.2.3.3) |
| **M3** | Interleaver | `interleave.py`: load/fetch for 40×{576,288,144,72,36,18}, 20×36, 10×9; bypass | spec fetch example; literal oracle; bijection/inverse; 75N step-7 variants |
| **M4** | MGD, scrambler, symbol formation | `mgd.py`, `scrambler.py`, `symbols.py` | Table VIII/IX exhaustive; Fig. 6 oracle; period 160; Table X/XIII orthogonality; exceptional 75-bps sets at 45/360 |
| **M5** | TX framer | `tx.py`: preamble → data/probe frames → EOM → flush; tribit stream out | golden tribit vectors per mode (committed); §4.3 invariants; empty message; INT-013 end-of-transmission policy |
| **M6** | Modulator + TX CLI | `modulate.py`, `io.py`, `m110c-tx` | constellation at symbol instants; occupied bandwidth within 300–3300 Hz; carrier = 1800 Hz ± 1 Hz; chunk invariance; memory bound. **→ v0.1: encoder complete** |
| **M7** | Genie-aided RX | demod with *known* timing/phase/mode: matched filter, descramble, soft demap, deinterleave, de-repeat, Viterbi, EOM | bit-exact noiseless loopback, all modes; AWGN BER vs theory. This proves the back half of RX before acquisition exists |
| **M8** | Acquisition **and minimal tracking** (scope widened 2026-10-12: ±0.2% clock drift cannot be decoded without it) | preamble search over ±400 Hz; timing, carrier, D1/D2/count; probe-aided (and 75-bps decision-directed) gain/phase/frequency/timing tracking; signal-loss detection; `m110c-rx` | random start offset, fractional timing; ±400 Hz offset and ±0.2% clock offset (§4.8); 3.5 Hz/s Doppler sweep; noise-only false alarms (and checks behind the threshold); back-to-back messages; INT-012/019/021 options. **Done** |
| **M9** | Channel simulator | `watterson.py`, `m110c-chan` | Appendix E.7 validation tests (SNR, bandwidth, Gaussian Doppler spectrum). **Done**: all E.7 checks met; Table XVI baseline recorded in PERFORMANCE.md. Cross-checked against PathSim 1.0's fading process (2026-10-12): the receiver results agree within the run-to-run spread (VALIDATION.md) |
| **M10** | Tracking + equalization | timing/carrier tracking; probe-trained DFE (LMS first for readability, RLS as an option) | 2-path static multipath; fading per Table XVI; equalizer convergence plots in the docs. **Done, with a change of design (2026-10-12):** prototypes showed that adaptive DFEs (LMS, RLS) cannot follow 5-Hz fading with the dozen taps a 5-ms channel needs, so the equaliser computes its coefficients from an LMMSE channel estimate (power-delay-profile prior) made at every probe and interpolated between probes, with two passes per frame; 75 bps uses the same estimates in a RAKE combiner. Every Table XVI row passes in short simulated runs (PERFORMANCE.md); 4800 bps now decodes on the real recording. Loss of signal is judged over 1 s, so fades no longer fragment messages. Convergence plots were replaced by the measurements in PERFORMANCE.md. The M8 tracker remains available (`--no-equalizer`) |
| **M11** | 75-bps receiver; acquisition in fast fading | slot reading that follows a fading phase; counts checked against the following segments | short preambles on every Table XVI fading channel (19–20/20 acquired, was 16–18 on 5 Hz); 75L at its Table XVI point decodes 16/16 channels (M10: 13, lost to misread counts); no change on AWGN. **Done** (PERFORMANCE.md). Equaliser tracking changes were tried and measured, and none kept. Not done: acquisition without a preamble (late entry from probes alone, stretch goal S5); a 75-bps detector beyond the RAKE (noise-limited at the SNRs where it matters) |
| **M12** | Streaming RX CLI + hardening | `m110c-rx` end-to-end; diagnostics on stderr (mode, SNR estimate, frequency offset, EOM) | §4.4 full suite; fuzzing; long-stream memory; **→ v0.9: decoder complete**. **Done** (PERFORMANCE.md, M12): BLAS threads limited per call (a program that imported NumPy first ran the receiver 3.5–4× slower than real time; now 0.1–0.4×); bounded memory over 10-minute streams; garbage input (random bytes as cf32, NaN, infinities, 3.4e38, malformed WAV headers) handled without exceptions or warnings; back-to-back messages of different rates (with 75 bps on either side), and a second message just before the input ends, now received; message end and SNR taken from the EOM; a stderr line when each message starts. Candidates for on-air use, still proposed and not part of M12: a TX sample-clock correction (`--tx-ppm`; sound cards can be tens of ppm off, the spec allows ±10 ppm, §4.2.1); radio-keying lead and hang times; in `--async` mode, idle stop bits with a keep-alive timeout instead of blocking (Appendix A send-all mode) |
| **M13** | Conformance + docs | Table XVI report, walkthrough notebook, SPEC_NOTES finalized | all perf tests run and recorded. **→ v1.0**. **In progress:** `tools/conformance.py` → CONFORMANCE.md (every Table XVI row, up to 10⁶ bits over many channels, 95% bounds); its first run found 75 bps losing lock on one-minute messages; a 75-bps tracking change halved the losses below the Table XVI SNR, but the 75-bps row still fails (3 of 223 one-minute messages lost; PERFORMANCE.md, M13); `docs/walkthrough.py` and the notebook built from it (run by the test suite); every SPEC_NOTES entry cited from code and tests, checked by a test |

Rough sizing: M0–M6 are mostly mechanical and spec-checkable; I expect each to take one or two
review cycles. M8 and M10 are the genuinely hard, research-flavored milestones and will take
several iterations. M10 in particular has no single "correct" answer, only performance
against Table XVI.

### Ordering rationale
- **TX before RX**: the transmitter is fully specified, while the receiver is not specified at
  all. The spec only defines what goes over the air. A frozen, golden-vectored TX is the
  reference that every RX stage is tested against.
- **Genie-aided RX (M7) before acquisition (M8)**: this separates "did we invert the TX chain
  correctly?" from "did we synchronize correctly?". Otherwise a failing loopback cannot tell the
  two apart.
- **Channel simulator before equalizer**: we can't tune an equalizer without the channel it must
  beat.

---

## 6. Stretch milestones (post-1.0, unordered)

- **S1**: Appendix C (3200–9600 bps, QAM, tail-biting K=7, mini-probes). This reuses
  `rx/acquire`, `rx/equalize` and `fec` with tail-biting added.
- **S2**: decoding real-world 110A/B/C recordings (§4.6) as a CI test.
- **S3**: rational resampler for arbitrary sample rates (8000 and 44100 S/s).
- **S4**: automatic zero/short interleave detection at RX, a design objective in §5.3.2.3.7.2.1.
- **S5**: 75-bps acquisition without a preamble, using the exceptional sets (§5.3.2.3.7.1.1 note).
- **S6**: C99 port, using the Python golden vectors as its oracle.
- **S7**: Appendix B (39-tone legacy) and Appendix D (WBHF).
- **S8**: the Appendix A TCP modem interface (data socket and remote control), so that
  software built for real modems, such as STANAG 5066 stacks, can use `m110c` as one.
- **S9**: interoperability tests against another implementation over an audio path
  (e.g. MS-DMT, a Windows 110A sound-card modem), across mode, interleave and framing.
  Until then, the claim is "symbol-exact with one fielded modem's recording", not
  "interoperable".

---

## 7. Risks

| Risk | Mitigation |
|------|-----------|
| A spec misreading baked into both TX and RX passes loopback but is not interoperable | Oracles written independently from the text (§4.2); external recordings (§4.6); SPEC_NOTES review |
| False EOM: the 32-bit EOM pattern occurs inside user data (≈ N/2³² per message, ~0.2% for 1 MB) | RX confirms EOM only when it is followed by flush zeros (INT-018). This is documented and tested with planted patterns |
| Equalizer tuning becomes a time sink | Table XVI sets the bar. Stop at "meets spec", not "optimal" |
| Python too slow for real time at 2400 bps with the long interleaver | Measured in M12: 0.1–0.4 s of one core per second of signal, once BLAS is kept to one thread per call (PERFORMANCE.md). C port (S6) remains the fallback |
| Mis-transcribed tables | Double entry plus the derived-quantity cross-checks in M1 |
| Our channel simulator flatters our receiver (same author, same reading of Appendix E) | E.7 checks (M9); receiver results repeated on PathSim 1.0's independently written fading process (VALIDATION.md). A shared misreading of Appendix E itself remains possible; the real recordings are the check against that |
| Settings both ends must agree on but the waveform does not announce (sync vs async framing, bit order) | Defaults follow the real recordings (INT-016, INT-025); documented as operator settings |
