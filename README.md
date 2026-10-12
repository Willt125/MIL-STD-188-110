# MIL-STD-188-110
A short, educational attempt at implementing a minimal MIL-STD-188-110C encoder/decoder.

The goal is a readable, heavily tested Python implementation of the fixed-frequency
serial-tone waveform (§5.3.2: 75–4800 bps, zero/short/long interleave). It produces
baseband I/Q from arbitrary data and recovers the data from baseband I/Q, via files or pipes.

- [`docs/PLAN.md`](docs/PLAN.md): scope, architecture, testing strategy and milestones.
- [`docs/VALIDATION.md`](docs/VALIDATION.md): the transmit chain checked against real 188-110A
  recordings. Our transmitter reproduces a fielded modem's output symbol for symbol.
- [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md): measured error rates (so far: the genie-aided
  receiver on AWGN, matching theory).
- [`docs/SPEC_NOTES.md`](docs/SPEC_NOTES.md): every ambiguity or error in the spec, and how
  this implementation reads it. Code and tests refer to these entries as `INT-nnn`.

## Status

| Milestone | State |
|-----------|-------|
| M0 Scaffolding | done |
| M1 Spec tables and derived mode parameters | done |
| M2 FEC encoder and Viterbi decoder | done |
| M3 Interleaver | done |
| M4 MGD, scramblers, symbol formation | done |
| M5 Transmit framer (+ golden vectors) | done |
| External validation of M1–M5 vs real recordings | done |
| M6 Modulator and `m110c-tx` CLI | done |
| M7 Genie-aided receiver | done |
| M8 Acquisition | next |

## Transmitting

```sh
pip install -e .
m110c-tx --rate 2400 --interleave long message.txt -o signal.cf32   # complex float32 I/Q
cat message.txt | m110c-tx --format wav > signal.wav                 # 16-bit audio WAV
m110c-tx --async 8N1 --format s16 < message.txt | aplay -f S16_LE -r 9600
```

The output is complex baseband at 9600 samples/s with the carrier at +1800 Hz, so the real part
is the audio for an upper-sideband transmitter. Options: `--rate` 75–4800, `--interleave`
zero/short/long, `--format` cf32/cs16/s16/wav, `--sample-rate` (a multiple of 2400),
`--carrier`, `--async 8N1` (start/stop framing, Appendix A send-all mode), `--msb-first`.
`m110c-tx --help` lists them all. The receiver's decoding chain exists (`m110c.rx`), but
synchronisation does not yet, so there is no `m110c-rx` command yet.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest               # fast tests
pytest -m slow       # performance and conformance tests (later milestones)
ruff check . && ruff format --check .
```
