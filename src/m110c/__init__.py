"""m110c: a readable, educational MIL-STD-188-110C serial-tone HF modem.

The package is organised to follow Figure 2 of the standard block by block. Spec
constants live in :mod:`m110c.spec`; ambiguities are recorded in ``docs/SPEC_NOTES.md``.
"""

import os

# The receiver solves thousands of small (about 40 x 40) linear systems per second. A
# multithreaded BLAS spends far longer starting threads for those than computing them,
# and under load it was measured 50x slower than one thread. These variables only take
# effect if NumPy has not been imported yet, and never override a value already set.
for _variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_variable, "1")

__version__ = "0.0.1"
