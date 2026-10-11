"""Literal transcription of Figure 6 (randomizing shift register functional diagram).

The figure draws twelve boxes, named here B1..B12 from left to right, holding the initial
pattern 1 0 1 1 1 0 1 0 1 1 0 1 (0xBAD). Arrows point left: on each shift every box takes
the value of the box to its right. Three XOR nodes sit in the arrows feeding B6, B8 and
B11. The value leaving B1 travels along the bottom line back to the right end, where it
enters B12 and also feeds all three XOR nodes. The outputs are taken from B10 (MSB),
B11 (middle bit) and B12 (LSB).

§5.3.2.3.8.1: load the initial pattern and shift eight times; read the three bits; shift
eight times for every further symbol; after 160 symbols reload the initial pattern
"prior to the eight shifts".
"""

INITIAL = [1, 0, 1, 1, 1, 0, 1, 0, 1, 1, 0, 1]  # B1 .. B12 as drawn
XOR_INTO = {"B6", "B8", "B11"}


def shift(boxes):
    """One shift of the drawing. ``boxes`` maps "B1".."B12" to bits."""
    leaving = boxes["B1"]
    new = {}
    for i in range(1, 12):
        incoming = boxes[f"B{i + 1}"]  # the box to the right
        if f"B{i}" in XOR_INTO:
            incoming ^= leaving
        new[f"B{i}"] = incoming
    new["B12"] = leaving
    return new


def sequence(n_symbols):
    out = []
    boxes = None
    for i in range(n_symbols):
        if i % 160 == 0:
            boxes = {f"B{k + 1}": bit for k, bit in enumerate(INITIAL)}
        for _ in range(8):
            boxes = shift(boxes)
        out.append(boxes["B10"] * 4 + boxes["B11"] * 2 + boxes["B12"])
    return out
