"""Build the notebooks in docs/ from their scripts, and execute them.

Usage:
    pip install -e ".[notebook]"
    python tools/build_notebook.py                 # all of them
    python tools/build_notebook.py channel         # one (walkthrough, channel, playground)

Each script (docs/<name>.py) is the source: cells are separated by ``# %%`` lines and
markdown cells start with ``# %% [markdown]`` (the "percent" format that Jupytext, VS Code
and Spyder read). The test suite runs the scripts themselves, so a notebook cannot go stale
unnoticed; rebuild it after changing its script, so that its saved outputs match.
Animations are written to docs/figures/ as GIFs and embedded in the notebooks.
"""

import pathlib
import re
import sys

import nbformat
from nbclient import NotebookClient

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"


def cells(source: str):
    for chunk in re.split(r"^# %%", source, flags=re.M)[1:]:
        header, _, body = chunk.partition("\n")
        body = body.strip("\n")
        if header.strip() == "[markdown]":
            text = "\n".join(
                line[2:] if line.startswith("# ") else line.lstrip("#")
                for line in body.splitlines()
            )
            yield nbformat.v4.new_markdown_cell(text)
        else:
            yield nbformat.v4.new_code_cell(body)


NOTEBOOKS = ("walkthrough", "channel", "playground")


def build(name: str) -> pathlib.Path:
    notebook = nbformat.v4.new_notebook()
    notebook.cells = list(cells((DOCS / f"{name}.py").read_text()))
    notebook.metadata["kernelspec"] = {
        "name": "python3",
        "display_name": "Python 3",
        "language": "python",
    }
    NotebookClient(
        notebook, timeout=1800, kernel_name="python3", resources={"metadata": {"path": str(DOCS)}}
    ).execute()
    path = DOCS / f"{name}.ipynb"
    nbformat.write(notebook, path)
    return path


def main(argv=None) -> None:
    names = (sys.argv[1:] if argv is None else argv) or NOTEBOOKS
    for name in names:
        if name not in NOTEBOOKS:
            raise SystemExit(f"unknown notebook {name!r}; choose from {', '.join(NOTEBOOKS)}")
    for name in names:
        print("wrote", build(name))


if __name__ == "__main__":
    main()
