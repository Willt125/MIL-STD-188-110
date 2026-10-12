"""Shared pytest configuration.

pytest puts this directory on ``sys.path``, which makes the independent reference
implementations importable as ``oracle.*`` from any test module.
"""

import m110c  # noqa: F401  (first, so its single-threaded BLAS setting applies; see there)
