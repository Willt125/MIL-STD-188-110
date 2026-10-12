"""Regenerate the transmit golden vectors in tests/vectors/tx_golden.json.

Usage:
    python tools/make_tx_vectors.py            # rewrite tests/vectors/tx_golden.json
    python tools/make_tx_vectors.py --full DIR # also write each mode's full tribit
                                               # stream to DIR/<mode>.txt (one digit
                                               # per symbol), e.g. for another
                                               # implementation to compare against

The vectors pin *this* implementation's output. They are our reading of the spec, not
values taken from it (the spec gives none). Regenerate them only when an intended change
to the waveform is made, and say why in the commit message.
"""

import argparse
import hashlib
import json
import pathlib

from m110c.spec import all_modes
from m110c.tx import transmit_bytes

#: Fixed test message: readable text, then every byte value once.
MESSAGE = b"MIL-STD-188-110C serial tone golden vector\n" + bytes(range(256))
OUT = pathlib.Path(__file__).resolve().parent.parent / "tests" / "vectors" / "tx_golden.json"
EDGE = 48


def vectors():
    result = {"message_hex": MESSAGE.hex(), "bit_order": "lsb-first", "modes": {}}
    for m in all_modes():
        tribits = transmit_bytes(MESSAGE, m)
        digits = "".join(map(str, tribits.tolist()))
        result["modes"][m.name] = {
            "length": len(digits),
            "sha256": hashlib.sha256(digits.encode()).hexdigest(),
            "head": digits[:EDGE],
            "tail": digits[-EDGE:],
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--full", type=pathlib.Path, help="also write full streams here")
    args = parser.parse_args()
    OUT.write_text(json.dumps(vectors(), indent=2) + "\n")
    print(f"wrote {OUT}")
    if args.full:
        args.full.mkdir(parents=True, exist_ok=True)
        for m in all_modes():
            digits = "".join(map(str, transmit_bytes(MESSAGE, m).tolist()))
            (args.full / f"{m.name}.txt").write_text(digits + "\n")
        print(f"wrote full streams to {args.full}")


if __name__ == "__main__":
    main()
