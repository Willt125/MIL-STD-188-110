"""Literal transcription of the interleave load (§5.3.2.3.4) and fetch (§5.3.2.3.5).

Uses a list-of-lists matrix and row/column counters, following the wording step by step.
The fetch reads the last row before resetting, and 75 bps uses steps of 7 (INT-007).
"""


def interleave(bits, rows, cols, load_row_step, fetch_col_decrement):
    assert len(bits) == rows * cols
    matrix = [[None] * cols for _ in range(rows)]

    # Load: "the first bit is loaded into row 0, the next bit is loaded into row 9 ...
    # the row location for the bits increases by 9 modulo 40. This process continues until
    # all 40 rows are loaded. The load then advances to column 1 and the process is
    # repeated until the matrix block is filled."
    i = 0
    for column in range(cols):
        row = 0
        for _ in range(rows):
            assert matrix[row][column] is None, "load wrote the same cell twice"
            matrix[row][column] = bits[i]
            i += 1
            row = (row + load_row_step) % rows

    # Fetch: "start with the first bit being taken from row zero, column zero. The location
    # of each successive fetched bit shall be determined by incrementing the row by one and
    # decrementing the column number by 17 (modulo number of columns) ... until the row
    # number reaches the maximum value. At this point, the row number shall be reset to
    # zero, the column number is reset to be one larger than the value it had when the row
    # number was last zero and the process continued until the entire matrix data block
    # is unloaded."
    out = []
    row, column = 0, 0
    column_when_row_was_zero = 0
    while len(out) < rows * cols:
        out.append(matrix[row][column])
        matrix[row][column] = None  # so a second read of the same cell is detectable
        if row == rows - 1:
            row = 0
            column = (column_when_row_was_zero + 1) % cols
            column_when_row_was_zero = column
        else:
            row += 1
            column = (column - fetch_col_decrement) % cols
    assert all(cell is None for line in matrix for cell in line), "fetch missed a cell"
    return out
