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
| M8 Acquisition, tracking and `m110c-rx` | done |
| M9 Watterson HF channel simulator (`m110c-chan`) | done |
| M10 Equalisation and fading-channel tracking | done |
| M11 75-bps refinements, acquisition in fades | next |

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
`m110c-tx --help` lists them all.

## Receiving

```sh
m110c-rx signal.cf32 -o message.txt
m110c-tx < message.txt | m110c-rx > copy.txt                       # a loopback through pipes
m110c-rx --format wav --async 8N1 recording.wav                    # e.g. a real 188-110 capture
```

The receiver finds each transmission by itself: it searches ±400 Hz around the 1800-Hz
carrier, reads the mode from the preamble, follows timing (±0.2% clock error) and carrier,
estimates the multipath channel at every probe and equalises it, and stops at each
end-of-message marker before searching for the next. A report per message goes to stderr.
Options: `--async`/`--msb-first` (must match the sender), `--zero-interleave`,
`--invert-spectrum` (lower-sideband input), `--max-offset`, `--sample-rate` (a multiple of 2400;
resample other rates first, e.g. `sox in.wav -r 9600 out.wav`), `--no-equalizer` (the simpler
single-gain tracker of M8, for comparison).

## Simulating an HF channel

```sh
m110c-tx < message.txt | m110c-chan --preset "2 fading 2 ms 1 Hz" --snr 18 | m110c-rx
m110c-chan --paths 2 --delay-ms 5 --doppler 5 --snr 7 --seed 3 in.cf32 -o out.cf32
```

The Watterson model of Appendix E: two equal-power paths, a delay spread apart, each with
Rayleigh fading of the given Doppler spread, plus noise at an SNR measured in 3 kHz. Presets
cover every Table XVI condition. Output is reproducible from `--seed`. The receiver meets every
Table XVI condition in the simulated runs recorded in docs/PERFORMANCE.md.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest               # fast tests
pytest -m slow       # performance and conformance tests (later milestones)
ruff check . && ruff format --check .
```
