"""Literal transcription of Figure 4 (FEC encoder block diagram).

The figure shows seven register cells labelled, left to right, x^6 x^5 x^4 x^3 x^2 x 1.
The input I(X) enters the x^6 cell; on each new bit everything moves one cell to the
right. The upper summing node forms T1 from the cells named in "T1 X6+X4+X3+X+1"; the
lower forms T2 from "T2 X6+X5+X4+X3+1". The output switch takes T1 first, then T2.
"""

from m110c.spec import tables as T

CELL_NAMES = ["x6", "x5", "x4", "x3", "x2", "x", "1"]
EXPONENT_TO_CELL = {6: "x6", 5: "x5", 4: "x4", 3: "x3", 2: "x2", 1: "x", 0: "1"}


def encode(bits):
    cells = {name: 0 for name in CELL_NAMES}
    t1_cells = [EXPONENT_TO_CELL[e] for e in T.GENERATOR_T1_EXPONENTS]
    t2_cells = [EXPONENT_TO_CELL[e] for e in T.GENERATOR_T2_EXPONENTS]
    out = []
    for bit in bits:
        # Shift right by one cell, then the new bit enters at x^6.
        for i in range(len(CELL_NAMES) - 1, 0, -1):
            cells[CELL_NAMES[i]] = cells[CELL_NAMES[i - 1]]
        cells["x6"] = int(bit)
        t1 = 0
        for name in t1_cells:
            t1 ^= cells[name]
        t2 = 0
        for name in t2_cells:
            t2 ^= cells[name]
        out.append(t1)
        out.append(t2)
    return out
