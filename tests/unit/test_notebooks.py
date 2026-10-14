"""The notebooks' scripts (docs/*.py) must keep running against the current code, quickly
(``M110C_QUICK``), and their animations must stay small enough to embed."""

import importlib.util
import pathlib
import runpy

import pytest

DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs"
TOOLS = DOCS.parent / "tools"
NOTEBOOKS = ("walkthrough", "channel", "playground")


@pytest.fixture
def notebook_env(monkeypatch, tmp_path):
    pytest.importorskip("matplotlib")
    import matplotlib

    matplotlib.use("Agg")
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("M110C_QUICK", "1")
    monkeypatch.setenv("M110C_FIGURES", str(tmp_path))
    monkeypatch.syspath_prepend(str(DOCS))
    monkeypatch.delitem(__import__("sys").modules, "nbtools", raising=False)
    import matplotlib.pyplot as plt

    plt.close("all")  # under Agg, plt.show() leaves figures open for the next script
    yield tmp_path
    plt.close("all")


@pytest.mark.parametrize("name", NOTEBOOKS)
def test_notebook_script_runs(name, notebook_env, capsys):
    runpy.run_path(str(DOCS / f"{name}.py"), run_name="__main__")
    out = capsys.readouterr().out
    if name == "walkthrough":
        assert "EOM found: True" in out and "identical to the message: True" in out


def test_animations_are_written_where_asked_and_stay_small(notebook_env):
    runpy.run_path(str(DOCS / "channel.py"), run_name="__main__")
    gifs = list(notebook_env.glob("*.gif"))
    assert gifs and all(g.read_bytes()[:6] in (b"GIF87a", b"GIF89a") for g in gifs)
    import nbtools

    assert all(g.stat().st_size <= nbtools.MAX_GIF_BYTES for g in gifs)


def test_an_animation_over_the_size_limit_is_refused(notebook_env, monkeypatch):
    import matplotlib.pyplot as plt
    import nbtools
    from matplotlib.animation import FuncAnimation

    monkeypatch.setattr(nbtools, "MAX_GIF_BYTES", 100)
    fig, ax = plt.subplots()
    (line,) = ax.plot([0, 1], [0, 1])
    anim = FuncAnimation(fig, lambda k: line.set_ydata([0, k]), frames=5)
    with pytest.raises(ValueError, match="fewer frames"):
        nbtools.save_gif(anim, "too_big")


def test_committed_animations_stay_small():
    """Each GIF is embedded in a committed notebook too: the repository carries it twice."""
    for gif in (DOCS / "figures").glob("*.gif"):
        assert gif.stat().st_size <= 1_500_000, gif.name


def test_the_build_splits_scripts_into_markdown_and_code_cells():
    pytest.importorskip("nbformat")
    spec = importlib.util.spec_from_file_location("build_notebook", TOOLS / "build_notebook.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    source = "# %% [markdown]\n# # Title\n#\n# text\n\n# %%\nx = 1\n\n# %%\nprint(x)\n"
    cells = list(build.cells(source))
    assert [c.cell_type for c in cells] == ["markdown", "code", "code"]
    assert cells[0].source == "# Title\n\ntext" and cells[2].source == "print(x)"
    assert set(build.NOTEBOOKS) == set(NOTEBOOKS)
