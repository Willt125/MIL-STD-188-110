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
   | 2400       | zero, short, long       | data; digital voice as raw bits (post-1.0) |
   | 1200       | zero, short, long       |                                         |
   | 600        | zero, short, long       |                                         |
   | 300        | zero, short, long       | rate-1/2 code repeated 2x               |
   | 150        | zero, short, long       | rate-1/2 code repeated 4x               |
   | 75         | zero, short, long       | 32-symbol orthogonal sets, no probes    |

2. **Decoder (`m110c-rx`)**: a baseband I/Q stream goes in, from a file or stdin. The original
   bytes come out, to a file or stdout. The decoder detects the mode from the preamble (D1/D2)
   automatically. Zero and short interleave, which the preamble cannot tell apart (SPEC_NOTES
   INT-012), are told apart from the data (S4).
3. **Pipe-able**: both tools run as streaming filters with bounded memory. The memory bound is
   independent of message length and dominated by one long-interleaver block.
4. **Readable and academic**: the code is organized to follow Figure 2 of the spec block-by-block.
   Every spec-derived constant cites its paragraph or table. Every interpretation of an ambiguous
   passage is recorded in [`SPEC_NOTES.md`](SPEC_NOTES.md) and referenced from code and tests.
5. **Heavily tested**: each functional block ships with unit tests, independent oracles, and
   round-trip/property tests before the next block builds on it (see §4).

### Non-goals (for v1.0)

- Frequency-hopping operation (§5.3.3). It is not the fixed-frequency waveform with pauses for
  the hops: 110C itself gives it a different code at 2400 bps (punctured rate 2/3, Table IV),
  one bit per symbol at 75 bps, other interleaver sizes at 300–75 bps, and a start-up in which
  the modem buffers up to 2.4 s of data until the *radio* signals clear-to-transmit (the radio
  owns the hopping; §5.3.2.2.1 d). The framing and timing within each hop are in
  MIL-STD-188-148, which 110C lists as SECRET; later editions are reportedly unclassified but
  still export-controlled. Implementing it in a public repository would publish controlled
  technical data, and there is no unrestricted reference to test against. Out of scope.
- Voice codecs. The modem side of 2400 bps digital voice (D1/D2 = 7,7) is supported after
  v1.0 as a raw bit stream (`--voice`, INT-019): the modem carries the vocoder's bits and an
  external program codes them. A codec is not included. §5.3.1.2 calls for STANAG 4198
  (LPC-10e) or STANAG 4591 (MELPe); MELPe's rates carry third-party IPR (TI at 2400 bps,
  Microsoft at 1200, Thales at 600, Compandent and AT&T for the noise pre-processor; some of
  it probably expired, not checked), STANAG 4591 itself is sold under export restrictions,
  and the open implementations found are GPL, which this MIT package cannot include.
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
| Pulse shape  | Root-raised-cosine, α = 0.15, transmit span ±16 symbols (α = 0.25, ±8 until 2026-10-20) | configurable α. The spec does not specify a filter (§5.3.2.3.9 note, INT-015). α = 0.15 is 2.75 kHz wide at -26 dB, inside the US amateur 2.8 kHz; wider α under the `authorized` profile |
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
| **M13** | Conformance + docs | Table XVI report, walkthrough notebook, SPEC_NOTES finalized | all perf tests run and recorded. **→ v1.0**. **Done:** `tools/conformance.py` → CONFORMANCE.md (every Table XVI row, up to 10⁶ bits over many channels, 95% bounds): every row meets its requirement, 600 bps is consistent with it. Getting there at 75 bps took three fixes, found by its one-minute messages (PERFORMANCE.md, M13): a slower, fade-gated timing loop; a set-correlation check on realignment (the M11 plan's set boundary sync); and long-preamble counts confirmed before use. `docs/walkthrough.py` and the notebook built from it (run by the test suite); every SPEC_NOTES entry cited from code and tests, checked by a test |

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

## 6. Stretch milestones (post-1.0)

**Order (agreed 2026-10-15).** The serial-tone stretch goals first: S4 (done), then S3 (done), then S5 (done).
Then the modem program (milestones to be written after the network-stack discussion,
docs/NETWORK.md):
real-time audio I/O and radio keying, the host interfaces and network stack (S8), a TUI.
Then the appendix modes: S1, then S7. S2 is reclassified: the acceptance tests already run
on the third-party recordings locally (`M110C_RECORDINGS`), and a CI version needs
recordings we may redistribute, which S9 would produce. S6 (C port) waits until the design
stops moving; S9 needs another modem over audio (e.g. MS-DMT) and the user's help. S10 is
done. Tools from S10 to use throughout: `tools/sweep.py` for measurements over channels,
`Receiver(trace=True)` for what the demodulator did, `TimeVaryingChannel` for conditions
that change, `tools/toystack.py` for link-layer experiments.


- **S1**: Appendix C (3200–9600 bps, QAM, tail-biting K=7, mini-probes). This reuses
  `rx/acquire`, `rx/equalize` and `fec` with tail-biting added.
- **S2**: decoding real-world 110A/B/C recordings (§4.6) as a CI test.
- **S3**: rational resampler for arbitrary sample rates (8000 and 44100 S/s).
  **Done** (2026-10-18): `m110c.resample`, a streaming polyphase resampler (exact ratio,
  Kaiser-windowed sinc, 80 dB stopband, no delay, chunk-invariant); the modulator and the
  receiver use it for any rate above 6800 S/s that is not a multiple of 2400
  (docs/PERFORMANCE.md, S3).
- **S4**: automatic zero/short interleave detection at RX, a design objective in §5.3.2.3.7.2.1.
  **Done** (2026-10-18): `rx/autointerleave.py`, the receiver's default; measured with
  `tools/interleave_check.py` (docs/PERFORMANCE.md, S4).
- **S5**: 75-bps acquisition without a preamble, using the exceptional sets (§5.3.2.3.7.1.1 note).
  Scope (2026-10-18): *late entry* at every rate, as the note itself says ("the receive modem
  shall use the modification of the known data at interleaver boundaries to synchronize
  without a preamble and determine the correct data rate and mode of operation"): the D1/D2
  probes before each block at 150-4800 bps, the exceptional sets at 75 bps.
  **Done** (2026-10-18): `rx/lateentry.py`, opt-in (`Receiver(late_entry=True)`,
  `m110c-rx --late-entry`); decisions in SPEC_NOTES INT-029, measurements in
  docs/PERFORMANCE.md (S5), `tools/late_entry_check.py` to repeat them. A search for the
  probe pattern (or the 75-bps sets) over +/-400 Hz, then the equalising demodulator in a
  late mode reading each probe and set, then every candidate boundary checked by decoding
  the block after it. Over 336 cut transmissions (every mode, noise only and fading): 318
  joined, none in the wrong mode, 1.5 s of signal with zero or short interleave and 3-7 s
  with long; noise alone (20 min) delivered nothing, and messages with preambles came out
  unchanged. On fading channels the late receiver is still worse than the preamble
  receiver in about 4% of receptions (11 of 288). Async framing finds its own
  characters where a block is not whole bytes (300, 150, 75 bps, zero or short).
- **S6**: C99 port, using the Python golden vectors as its oracle.
- **S7**: Appendix B (39-tone legacy) and Appendix D (WBHF).
- **S8**: host interfaces and a link layer.
  - The Appendix A LAN interface (TCP and UDP, data and remote control). It is part of 110C
    (non-mandatory) and depends on no other document.
  - A KISS interface (TCP and pipe), the de facto host interface of open packet software.
  - On air, a minimal frame inside each transmission (sync word, length, payload, CRC), so a
    damaged frame costs only itself. Link layers on top are existing open protocols chosen for
    110's behaviour (half-duplex, seconds per turnaround, long one-way transmissions):
    AX.25 UI frames, store-and-forward, erasure-coded broadcast. Not TCP end to end.
  - `tools/toystack.py` is the playground for these choices: six traffic patterns as real
    transmissions through the channel simulator and the receiver. First results (one
    channel realisation each, 2 paths, 2 ms, 1 Hz): every transmission costs at least 1.2 s,
    so control traffic belongs inside data transmissions; long interleave never takes less
    than 9.6 s, too long for short messages; and at 11 dB a 16-kB file arrived faster at
    1200L, with no losses (875 bps end to end), than at 2400L repaired by a fountain code
    (about 560-670 bps). Choosing the 110 mode from measured SNR matters more than repair.
  - Negotiating the mode (the tool's `negotiated` pattern, README.md as the file): the
    receiver's SNR report reads 3-4 dB below the channel SNR, consistently, and one short
    message is good to about +/-1.5 dB, so it is usable once calibrated and given a margin.
    SNR alone overreacts: after a band drop, one low estimate sent a transfer from 1200L to
    150L although 1200L had just delivered every block. Moving at most one rung after a
    clean round, and jumping down only after real losses, took that run from 437 to 780 bps
    end to end. Feedback needs short data transmissions (here 6 blocks) and polls for lost
    status replies.
    The playground notebook's rise-and-fall run (20 dB to 6 and back, 14 kB) shows the
    policy's next limits: a round of six blocks lasts 14 s at 1200L but 82 s at 150L, so
    decisions arrive late exactly when the rate is low (a 150L round chosen at 6 dB was
    sent while the SNR climbed back to 20), and one rung per clean round makes recovery
    slow (about 200 s from 150L back to 2400L). Next to try: rounds sized in seconds rather
    than blocks, and upward jumps of more than one rung after a clearly better estimate.
  - Conditions that change: `TimeVaryingChannel` (our extension of Appendix E, identical
    to it with constant schedules) carries a whole toystack session on one continuous
    channel, with SNR and path powers following straight-line or random-walk (Brownian
    bridge) schedules. Not yet in `m110c-chan`'s command line. Doppler-spread and delay
    changes are not modelled (a moving delay would also need a per-path Doppler shift).

  **[DECIDED]** No STANAG 5066, now or later: its current editions are not publicly
  distributable, and earlier ones differ on the air (the CRCs changed after the 1.0.2 draft).
  Note that 110C §4.4 says an ARQ protocol, if used, "shall be in accordance with STANAG
  5066": an ARQ layer of ours is outside 110C, and the docs will say so. The waveform and
  Appendix A are unaffected. Reticulum fits technically but encrypts by design, which
  amateur rules forbid (e.g. US Part 97); usable only where encryption is allowed.
- **S9**: interoperability tests against another implementation over an audio path
  (e.g. MS-DMT, a Windows 110A sound-card modem), across mode, interleave and framing.
  Until then, the claim is "symbol-exact with one fielded modem's recording", not
  "interoperable".
- **S10**: notebooks that explain the standard, the channel and the link-layer experiments
  (see "Notebooks" below).

### Notebooks (S10)

**[DECIDED]** Three notebooks, each a py:percent script (the source) built into an `.ipynb`
with outputs by `tools/build_notebook.py`, and each run by the test suite:

1. **`walkthrough`** (exists): the standard, block by block, one message. To add: a ribbon
   per mode (data/probe pattern, preamble length); preamble anatomy (prefix, D1/D2, count,
   the countdown); constellations with their bit labels (8-PSK MGD, 1- and 2-bit subsets);
   scrambling (spectrum of all zeros before and after); the RRC pulse and an eye diagram;
   the 75-bps sets and their correlation matrix (exceptional sets); a burst of channel
   errors before and after deinterleaving; coding gain (BER vs SNR, coded and uncoded).
2. **`channel`** (new): the Watterson model (fading traces, Doppler spectrum vs Appendix E's
   Gaussian, Rayleigh statistics), the channel's impulse response over time, schedules
   (lines and walks), Table XVI performance from CONFORMANCE.md's data, and **matching the
   mode to the conditions**:
   - interleave vs fade duration: 1200 bps at zero/short/long interleave on 0.5, 1 and
     5 Hz fading, with a fading trace under the 0.6-s and 4.8-s blocks;
   - rate vs SNR: goodput of every rate against SNR, and the crossovers that make
     negotiation worth having;
   - delay spread vs the probe window (16 symbols, 6.7 ms at 2400 bps): performance
     against delay spread 0-8 ms;
   - Doppler spread vs probe density (32/16 frames at 2400 bps, 20/20 at 600): performance
     against Doppler spread 0.5-10 Hz; and where 75 bps (no probes) fits;
   - named conditions (ITU-R F.1487's latitude/disturbance table and flutter, values to be
     checked against the document) with the mode each favours.
   These are our receiver's results, not limits of the standard, and the section says so.
   Their data comes from a new `tools/sweep.py` (like `tools/conformance.py`, hours to run)
   into `docs/data/sweep.json`, which the notebook plots, so the notebook stays fast.
3. **`playground`** (new): the link-layer experiments of `tools/toystack.py`: frame anatomy,
   session timelines (who transmits when), the negotiation figure (SNR schedule, measured
   SNR, mode chosen, losses on one time axis), fountain decoding progress, and payload
   efficiency against message size per mode (analytic).

**[DECIDED]** Animations are GIFs (matplotlib's Pillow writer), written to `docs/figures/` by
the build and shown as images: GitHub's notebook viewer strips JavaScript, so in-notebook
animations would not play there. Kept small (about 1 MB or less each). Candidates: the
interleaver loading and fetching, Viterbi survivor paths, the equaliser's constellation
settling, the channel's impulse response fading.

Each notebook script runs quickly by default (CI runs it); heavy figures read precomputed
data or use a reduced setting. The receiver figures (equaliser, tracking loops) need an
optional trace from the demodulator, off by default and tested, rather than reading private
attributes; it is the one change to modem code.

Order: (1) the multi-notebook build, GIF support and tests; (2) the walkthrough's static
figures; (3) the channel notebook, with `tools/sweep.py` and the mode-matching section;
(4) the playground notebook; (5) animations, the demodulator trace last. All five are
done (2026-10-14). The trace is `Receiver(trace=True)`: each message's `trace` lists the
demodulator's state after every unit (channel estimate, alignment, carrier, clock, unit
SNR, the data symbols' estimates), off by default and changing nothing when on.

Receiver weaknesses found by the sweep (`tools/sweep.py`, 2026-10-14), both fixed:
- **A mode misread from one faded preamble segment.** A 1200S message (5 Hz, 14 dB) was
  received as 75S: D1 = 6 read as 7. Segments that disagreed on D1/D2 were taken as "another
  mode's", never as evidence against the first reading, and a short preamble's count needs
  no confirmation. *Fixed:* the following segments whose count fits now vote on D1/D2; the
  majority wins, and a tie waits for the next segment (`PreambleSearch._refine_and_read`).
- **A message lost to a path swap.** A 1200Z message (0.5 Hz, 20 dB) was declared "signal
  lost" after 1 s and the rest discarded. Not a fade (the total power never fell below
  -8 dB): through the preamble the first path was 11-13 dB below the second, so tap 0 was
  put on the second; when the paths swapped power, the first was 5 symbols before tap 0,
  outside the equaliser's span, the decisions failed, and the delay profile, fitted to
  those decisions, never showed the path that would have moved tap 0. *Fixed:* in the data
  phase a second delay profile comes from the probes alone (`_update_probe_profile`), which
  does not depend on the decisions; when 15% of its power lies before the equaliser's span,
  tap 0 moves to its leading edge, and it vetoes the slow decision-fitted profile's move
  back (`_probe_check`). Its leading edge is not used otherwise: the probe repeats with the
  scrambler, so its correlation has steady sidelobes up to about -10 dB (used alone, it
  lost a 300L message to one). On fixed paths swapping in 0.5-4 s (2 and 5 ms, 20 dB,
  20-s messages), 2400L had lost all eight cases and 1200L the fastest 5-ms one; now none
  is lost. A shorter profile memory alone (1 or 0.5 s instead of 2 s) rescued only the
  slower swaps, and on random Watterson channels changed nothing, so it stays at 2 s.
- Found with the demodulator trace (S10 step 5): the per-frame probe SNR used for loss of
  signal saturates near 11-12 dB (median, clean 2400S channel at 18 or 30 dB), though its
  docstring had called it nearly unbiased. Harmless for the -6 dB loss decision; it is not
  a measurement, and the docs now say so. Worth understanding before anything else uses
  it (a link layer's SNR report should use the decoder's estimate).
- The delay-spread limit (clean to 7-8 ms, gone from 9 ms) is the equaliser's chosen span
  (`POSTCURSOR_TAPS`, 8.3 ms), not a defect; noted in the channel notebook.
- **Known weakness, not fixed (found 2026-10-20):** where a transmission that stops
  mid-message (signal lost, no EOM) is placed to have ended depends on where the cut falls.
  1200L cut off into noise, 21 cuts between 55% and 65% of the message: 18 placed within
  0.01 s, 3 between 0.29 and 0.64 s late, with the old and the new pulse shapes alike. The
  end is placed where the final run of bad frames began, and one noise frame scoring as
  signal (the per-frame fraction is noisy, about +/-0.2) restarts that run. It matters only
  for where the next search starts. A smoothed fraction, or a run that tolerates one good
  frame, would fix it; to be measured first.


### The network stack: agenda for a discussion before the modem program

**Discussed 2026-10-19: see [NETWORK.md](NETWORK.md)** for the goals, the decisions (amateur
first and legal by default; Winlink interoperability through B2F and an ARDOP-compatible host
interface) and the open questions. The agenda below is kept as recorded.

**[TO DISCUSS]** Recorded 2026-10-15 from the user's goals; nothing here is decided. The
aim is a stack that is an educational toy and still usable day to day, as chibicc and QOI
are. The layer names are borrowed from STANAG 5066 as vocabulary only: nothing of 5066's
design is to be read further or copied, and nothing will claim compatibility with it. Each
layer is judged by the goal it serves in a civilian or research setting, then met by an
existing public protocol where one fits, or by something of our own.

Layers, as goals:
- **Subnetwork interface**: clients attach and are served; soft-link, hard-link and
  broadcast data exchange sessions; a ranking of clients by importance.
- **Channel access**: makes and breaks physical links, reports link state, passes data
  frames between the layers above and below.
- **Data transfer**: an ARQ and a non-ARQ mode, both with regular and expedited service;
  ARQ also confirms delivery (to the node and to the client) and delivers only error-free
  frames.
- **Subnet management**: reaches into every layer, into ALE (if any) and into the modem
  itself (mode choice, link quality: toystack's negotiation lives here).

Clients to consider: unacknowledged and acknowledged messages; mail in the manner of HMTP
and HF-POP3 (SMTP and POP3 adapted to HF: batching, compression, few turnarounds); ZMODEM
file transfer; orderwire (operator chat); subnet management clients; frequency-availability
information clients.

Questions to bring to it:
- **Which layers earn their place.** The open amateur HF stacks (VARA, ARDOP, PACTOR with
  Winlink's B2F) are flatter: an ARQ link plus a session protocol. Merge layers where a
  civilian use needs no split.
- **Public protocol or our own, layer by layer.** HMTP and HF-POP3 are themselves 5066
  client definitions, so "our own SMTP/POP3 adaptation" or Winlink's documented B2F;
  ZMODEM's protocol description is public; AX.25 and KISS give interoperability with
  existing software, which a bespoke protocol never has.
- **Size, the chibicc/QOI way.** One short specification per layer of our own, with a
  reference implementation, test vectors, and a toystack experiment before it is built.
- **Rules.** Amateur use needs publicly documented protocols and no encryption (e.g. US
  Part 97): publishing our own specifications is then a requirement, and enough.
- **ALE and frequency availability.** MIL-STD-188-141 (ALE) is believed to be approved for
  public release (to be checked), with open implementations; link-quality data from it, or
  open propagation prediction (e.g. VOACAP), could feed frequency-availability clients.
- **Open findings to carry in**: toystack's round sizing and recovery (S8 above); the
  per-frame probe SNR that saturates near 11-12 dB (§5 notes), not to be reported upward.
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
