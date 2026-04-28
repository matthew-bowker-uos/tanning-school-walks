"""End-to-end audit tests against an isolated DATA_ROOT."""

from __future__ import annotations

from pathlib import Path

import pytest


def _write_raw(root: Path, rel: str, content: bytes) -> Path:
    path = root / "raw" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_register_and_verify_roundtrip(isolated_data_root: Path) -> None:
    from schools_sunbeds import audit

    src = _write_raw(isolated_data_root, "demo/2026-04-28/file.txt", b"hello world")

    row = audit.register_raw_file(
        src,
        source_url="https://example.org/file.txt",
        licence="OGL v3.0",
        notes="unit test",
    )

    assert row["sha256"] == (
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    )
    assert row["size_bytes"] == "11"
    assert audit.verify_manifest() == []


def test_verify_detects_drift(isolated_data_root: Path) -> None:
    from schools_sunbeds import audit

    src = _write_raw(isolated_data_root, "demo/2026-04-28/file.txt", b"hello world")
    audit.register_raw_file(
        src, source_url="https://example.org", licence="OGL v3.0", set_readonly=False
    )

    src.chmod(0o644)
    src.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="hash drift"):
        audit.verify_manifest()


def test_provenance_sidecar_written(isolated_data_root: Path, tmp_path: Path) -> None:
    from schools_sunbeds import audit

    out = isolated_data_root / "processed" / "result.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\x00\x01\x02")

    inp = isolated_data_root / "raw" / "demo" / "in.csv"
    inp.parent.mkdir(parents=True, exist_ok=True)
    inp.write_bytes(b"col\n1\n")

    sidecar = audit.write_provenance_sidecar(
        audit.Provenance(output_path=out, inputs=(inp,), notes="test", random_seed=42)
    )

    assert sidecar.exists()
    text = sidecar.read_text()
    assert "library_versions" in text
    assert "random_seed: 42" in text
