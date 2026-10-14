"""Helpers shared by the notebook scripts in docs/ (walkthrough.py, channel.py, playground.py).

* ``QUICK``: set by the test suite (``M110C_QUICK=1``) so that each script runs in seconds.
  Figures that need long simulations use a smaller setting then; the committed notebooks are
  built with the full one.
* :func:`save_gif`: animations are saved as GIFs in ``docs/figures/`` (or wherever
  ``M110C_FIGURES`` points; the tests use a temporary folder) and shown inline. GitHub's
  notebook viewer strips JavaScript, so an interactive animation would not play there; an
  image does.
"""

import os
import pathlib

DOCS = pathlib.Path(__file__).resolve().parent
QUICK = os.environ.get("M110C_QUICK") == "1"
FIGURES = pathlib.Path(os.environ.get("M110C_FIGURES", DOCS / "figures"))
MAX_GIF_BYTES = 1_500_000  # each animation is embedded in its notebook: keep them small


def save_gif(animation, name: str, fps: int = 10, dpi: int = 72) -> pathlib.Path:
    """Write ``animation`` (a matplotlib Animation) to FIGURES/name.gif and show it."""
    import matplotlib.pyplot as plt
    from matplotlib.animation import PillowWriter

    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / f"{name}.gif"
    animation.save(path, writer=PillowWriter(fps=fps), dpi=dpi)
    plt.close(animation._fig)
    size = path.stat().st_size
    if size > MAX_GIF_BYTES:
        raise ValueError(f"{path.name} is {size / 1e6:.1f} MB: use fewer frames or a lower dpi")
    try:
        from IPython import get_ipython
        from IPython.display import Image, display
    except ImportError:
        get_ipython = None
    if get_ipython is not None and get_ipython() is not None:
        display(Image(filename=str(path)))  # embedded in the notebook's output
    else:
        print(f"animation: {path} ({size / 1e3:.0f} kB)")
    return path
