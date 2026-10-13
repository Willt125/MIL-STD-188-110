"""Run the receiver's linear algebra on one BLAS thread.

The equaliser solves thousands of small (about 40 x 40) linear systems per second. A
multithreaded BLAS spends far longer waking its threads for those than computing them:
the whole receiver ran 3.5-4x slower than real time with OpenBLAS's default threads on a
loaded 4-core machine, and 0.1-0.4x real time with one (docs/PERFORMANCE.md, M12).

Environment variables such as OPENBLAS_NUM_THREADS only work if they are set before NumPy
is first imported, which a library cannot count on, so the limit is applied around each
call instead (about 6 microseconds) and the caller's own setting is restored afterwards.
"""

import functools

from threadpoolctl import ThreadpoolController

_controller = None  # created on first use, after NumPy (and its BLAS) has been loaded


def single_threaded_blas(method):
    """Decorator: run ``method`` with every BLAS library limited to one thread."""

    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        global _controller
        if _controller is None:
            _controller = ThreadpoolController()
        with _controller.limit(limits=1, user_api="blas"):
            return method(*args, **kwargs)

    return wrapper
