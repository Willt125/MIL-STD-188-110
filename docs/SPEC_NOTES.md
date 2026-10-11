# Spec Interpretation Register

This file records every place where MIL-STD-188-110C w/Change 1 (the PDF in this repo) is
ambiguous, internally inconsistent, or silent, and what this implementation does about it.
Code that depends on an entry carries a `# INT-nnn` comment. Tests that pin the behavior are
named `test_int_nnn_*`.

**Kinds**
- **Errata**: the spec contradicts itself. We pick the majority or most-specific reading.
- **Interpretation**: the text admits more than one reading. We pick one and justify it.
- **Unspecified**: the spec leaves it to the implementer. We make a design choice that does
  not affect over-the-air interoperability, or affects it only in a documented way.

**Confidence**: High / Medium / Low. Low-confidence entries are top priority for external
validation against real recordings (PLAN §4.6).

All paragraph and table numbers refer to the main body (§5.3.2) unless noted otherwise.

---

### INT-001: Table XII contradicts its own text (count-symbol mapping). *Errata, High*
§5.3.2.3.7.2.1 says each 2-bit count value is converted to 3 bits by prefixing a `1` as the
MSB, which gives `00→4, 01→5, 10→6, 11→7`. The worked note agrees: count 23 = `010111`
gives C1,C2,C3 = 5,5,7. **Table XII, however, lists `10→5` and `01→6`.** By that table, 23
would encode as 6,6,7.
**Decision:** follow the text and the note (symbol = 4 + two-bit value). Table XII looks like it
was typeset with the two-bit column written LSB-first.
**Hedge:** in a well-formed preamble the counts must decrement by one per segment. The RX can
evaluate both mappings and report which one is consistent, which turns a real-world recording
into a test of this entry.

### INT-002: Convolutional encoder register orientation. *Interpretation, Medium*
Figure 4 labels the register cells `x^6 … x, 1`, with the input `I(X)` entering the `x^6`
cell. Read literally, `x^6` is the newest bit (delay 0) and `1` is the oldest (delay 6). That
gives tap delays T1 = {0,2,3,5,6} and T2 = {0,1,2,3,6}, which are octal 133 and 171 with the
MSB as the current bit. The other reading (`x^k` = delay *k*) gives the time-reversed taps.
Both codes have d_free = 10, so loopback cannot tell them apart. Only interoperability can.
**Decision:** use the literal figure reading (133/171 octal, T1 output first). This matches the
generator pair commonly quoted for this waveform, which still needs confirming against a
recording.
**Tests:** `fec.py` derives the taps with a delay formula. The oracle in
`tests/oracle/conv_encoder.py` transcribes the figure's cells literally, so the two must agree.
The mutation pass confirms that the alternative (time-reversed) reading fails the suite.

### INT-003: Data scrambler structure (Figure 6). *Interpretation, High*
The text says only "12-bit shift register"; the structure is in Figure 6. Reading the figure:
the register shifts toward the MSB. The bit leaving cell 11 is fed back into cell 0 and XORed
into the inputs of cells 1, 4 and 6. That is a Galois LFSR with polynomial
x¹² + x⁶ + x⁴ + x + 1. The output tribit is (cell 2, cell 1, cell 0) = (MSB, middle, LSB).
The initial state is `1011 1010 1101` = 0xBAD, with cell 11 as the leftmost bit.
**Decision:** as above. The register is advanced 8 times *before* the first output and 8 times
between outputs. It is reloaded with 0xBAD every 160 symbols, before that symbol's 8 shifts.
**Tests:** a bit-level oracle drawn from the figure; a period of exactly 160; the first several
outputs checked by hand in the test file's docstring.

### INT-004: Data scrambler alignment. *Interpretation, High*
**Decision:** the 160-symbol period counts every transmitted data-phase symbol (unknown *and*
known/probe, and the 75-bps sets). It starts at the first symbol after the preamble and does not
restart at interleaver-block boundaries. §5.3.2.3.8.1 says "each transmit symbol period", and
§5.3.2.3.7.1 scrambles all symbol-formation output.

### INT-005: Where the D1/D2 probe patterns go. *Interpretation, Medium*
§5.3.2.3.7.1.2: the "two known symbol patterns preceding the transmission of each new
interleaved block" carry D1 and D2.
**Decision:** the *last two probe slots* of each interleaver block (1440 or 11520 symbols, with
the first block starting right after the preamble) carry D1 and then D2. All other probe
symbols are 0. The first block has no D1/D2 in front of it, because the preamble precedes it.
The final block of a transmission still carries them, for uniformity. The RX must not
*require* them on the final block.

### INT-006: D1/D2 probe contents. *Interpretation, High*
**Decision:** each D value is used as a 3-bit channel symbol and mapped through Table XIII, but
using only the first 16 tribits (the 8-tribit pattern repeated twice instead of four times). In
20-symbol probe slots (600–150 bps, and 1200 bps), the remaining 4 tribits are 0. Zero
interleave and 4800 bps use the 1440-symbol (short) block period for D1/D2 placement, per the
note in §5.3.2.3.7.1.2.

### INT-007: 75-bps (fixed-frequency) interleaver variants. *Interpretation, Medium*
Table VI gives 20×36 (long) and 10×9 (short). Load: the row advances by 7 mod rows. Fetch: the
column decrements by 7 (not 17) mod cols. The general fetch rule ("row reset to zero, column
reset to one larger than when row was last zero") is otherwise unchanged.
**Tests:** permutation bijectivity for both sizes (gcd(7,20) = gcd(7,10) = 1 for load, and the
fetch visits every cell); a literal oracle.

### INT-008: Repetition order at 150/300 bps. *Interpretation, High*
"Repeated in pairs": output is T1 T2 T1 T2 … (2× or 4×), not T1 T1 … T2 T2. Repetition is
applied *before* the interleaver. Table VI sizes confirm this: 300 bps × 4 × 0.6 s = 720 =
40 × 18.

### INT-009: Bit grouping into channel symbols. *Interpretation, High*
The first bit fetched from the interleaver is the "first bit" (MSB) in Tables VIII/IX.

### INT-010: 75-bps exceptional sets. *Interpretation, Medium*
"Every 45th set (short) / 360th set (long) following the end of the sync pattern" is the
*last* 32-tribit set of each interleaver block (45 = 90/2 and 360 = 720/2 dibits per block).
**Open:** at 75 bps with zero interleave (bypassed), the spec does not say whether exceptional
sets occur. **Decision:** keep the 45-set period (the short-interleave pattern), consistent
with INT-006.

### INT-011: EOM bit order. *Resolved by text, High*
0x4B65A5B2 is sent with the most significant digit first: `0100 1011 0110 0101 …`.

### INT-012: Zero and short interleave are indistinguishable at RX. *Unspecified, High*
Table XI assigns the same D1/D2 to both. The note says the two ends must coordinate, and lists
automatic selection as a design objective.
**Decision (v1):** RX assumes short; `--zero-interleave` overrides. Automatic detection is
stretch goal S4. One approach is to decode the first block both ways and compare Viterbi path
metrics.

### INT-013: End-of-transmission framing. *Unspecified, Medium*
- With an interleaver: EOM (32 bits) plus 144 flush zero bits, then zeros up to the end of
  the interleaver block containing the last flush bit (§5.3.2.3.2). A block boundary is always
  a frame boundary, so nothing more is needed.
- Zero interleave: "only the coder flush bits are transmitted". After the 144 flush bits,
  **we pad with zero channel bits to complete the current frame** (unknown + probe). The spec
  does not say how to end a partial frame.
- 4800 bps: there is no coder, but the spec still implies a flush phase. **Decision:** send
  EOM, then 144 zero bits uncoded, then pad to the frame boundary.

### INT-014: Start of data phase in an offline encoder. *Unspecified, High*
The real-time narrative (load during the preamble) is equivalent to: preamble, then block 1,
block 2, and so on. The encoder is not real-time paced, so it simply waits for input. With
zero interleave, data symbols start immediately after the 0.6 s preamble.

### INT-015: Transmit pulse shape. *Unspecified, High*
§5.3.2.3.9 says only that the I/Q waveforms "must be filtered". **Decision:** root-raised-cosine,
α = 0.25 (default), giving 1800 ± 1500 Hz = 300–3300 Hz. The receiver does not need to know
the transmitter's filter, because the equalizer absorbs the mismatch. Configurable.

### INT-016: Byte to bit order at the user interface. *Unspecified, High*
The synchronous serial interface carries bits, not bytes. **Decision:** LSB-first within each
byte (UART convention), with `--msb-first` available. A message is always a whole number of
bytes. RX drops any trailing partial byte before the EOM.

### INT-017: Input underrun. *Unspecified, High*
The spec defines no idle fill. The encoder blocks on input. EOF means the end of the message
(RTS dropped), which triggers the EOM phase.

### INT-018: False EOM detection inside user data. *Unspecified, High*
The EOM is not escaped. Any 32-bit window of user data equal to 0x4B65A5B2 would truncate the
message. That is about N/2³² per message: roughly 0.2% for 1 MB.
**Decision:** RX accepts an EOM candidate only if it is followed by flush zeros, all 144 coded
flush bits after decoding (with a small error allowance), or by the end of the signal. RX
treats an unconfirmed candidate as data.
**Tests:** a message with the EOM pattern planted at, and off, byte alignment.

### INT-019: Unimplemented D1/D2 combinations. *Resolved by text, High*
RX treats 7,7 (digital voice), reserved (long + 4800/voice) and 5,6/5,7 (application-specific)
as "not implemented". It keeps searching rather than synchronizing on them.

### INT-020: Symbol formation maps (Figure 5). *Interpretation, High*
One-bit symbols: 0→0, 1→4. Two-bit symbols (after the MGD): `00→0, 01→2, 10→4, 11→6`.
Three-bit symbols: identity. Figure 5 shows this; the text (§5.3.2.3.7.1.1) gives only the sets
{0,4}, {0,2,4,6} and {0..7}.

### INT-021: Phase rotation sense and sideband. *Unspecified, Medium*
Tribit k is sent as phase +45°·k, counter-clockwise, so complex baseband symbol = e^{jπk/4}.
The audio is Re{s(t)·e^{j2π·1800t}}. Feeding the audio to an LSB transmitter, or reading I/Q
with I and Q swapped, mirrors the spectrum. The result looks like 8PSK with the rotation sense
reversed, so k becomes −k mod 8. RX will offer `--invert-spectrum`. Acquisition could also try
both senses, because the preamble correlation fails cleanly in the wrong one.

### INT-022: How 4800 bps is labelled. *Unspecified, High*
4800 bps has no interleaver (§5.3.2.3.4 note). It uses the short-interleave D1/D2 (Table XI;
the long column is "reserved") and the short-interleave probe pattern (§5.3.2.3.7.1.2 note).
It therefore behaves exactly like a zero-interleave mode. **Decision:** 4800 bps is represented
internally as `interleave="zero"`, so it gets the 3-segment (0.6 s) preamble. Asking for 4800 bps
with long interleave is an error.

### INT-023: Encoder register at the start of a transmission. *Unspecified, High*
The spec does not say what the convolutional encoder's shift register holds before the first
data bit. **Decision:** it starts cleared (all zero), the conventional choice, and the same state
the coder flush leaves behind. The RX Viterbi decoder therefore starts in state 0. It can also
start with the state unknown (`start_state=None`), at the cost of a few unreliable leading bits
(tested). If a real recording shows otherwise, only the starting state changes.

---

## Candidate errata to report upstream
- INT-001 (Table XII bit order).
- §5.3.2.3.7.2.2 note: "to produce 8 pattern of 16 tribit numbers" means "a pattern".
- The Appendix E table of contents has a broken cross-reference ("Error! Bookmark not defined").
