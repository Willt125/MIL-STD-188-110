"""Build docs/walkthrough.ipynb from docs/walkthrough.py and execute it.

Usage:
    pip install -e ".[notebook]"
    python tools/build_notebook.py

The script is the source: cells are separated by ``# %%`` lines and markdown cells start
with ``# %% [markdown]`` (the "percent" format that Jupytext, VS Code and Spyder read).
The test suite runs the script itself, so the notebook cannot go stale unnoticed; rebuild
it after changing the script, so that its saved outputs match.
"""

import pathlib
import re

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


def main() -> None:
    notebook = nbformat.v4.new_notebook()
    notebook.cells = list(cells((DOCS / "walkthrough.py").read_text()))
    notebook.metadata["kernelspec"] = {
        "name": "python3",
        "display_name": "Python 3",
        "language": "python",
    }
    NotebookClient(
        notebook, timeout=600, kernel_name="python3", resources={"metadata": {"path": str(DOCS)}}
    ).execute()
    nbformat.write(notebook, DOCS / "walkthrough.ipynb")
    print("wrote", DOCS / "walkthrough.ipynb")


if __name__ == "__main__":
    main()
