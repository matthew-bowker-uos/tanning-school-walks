"""Test fixtures.

The audit tests need a writable ``DATA_ROOT`` so they don't touch the real
project ``data/``. We point ``DATA_ROOT`` at a per-test ``tmp_path`` and
re-import the audit module so its module-level constants pick up the new
root.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def isolated_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point DATA_ROOT at a tmp dir and reimport config + audit."""

    monkeypatch.setenv("DATA_ROOT", str(tmp_path))

    import schools_sunbeds.config as cfg

    importlib.reload(cfg)
    cfg.ensure_dirs()

    import schools_sunbeds.audit as audit

    importlib.reload(audit)

    return tmp_path
