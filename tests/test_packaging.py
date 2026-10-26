"""Guard the published package surface (#124)."""

import importlib.util
from pathlib import Path

import user_control_probe

import pysolarcloud


def test_probe_module_is_not_part_of_the_published_package():
    """The experimental probe lives in the test tree only; the package must not ship or expose it."""
    assert importlib.util.find_spec("pysolarcloud.user_control_probe") is None
    assert not (Path(pysolarcloud.__file__).parent / "user_control_probe.py").exists()
    # ...but it is importable from the test tree.
    assert Path(user_control_probe.__file__).parent == Path(__file__).parent
