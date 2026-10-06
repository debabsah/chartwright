"""The Sundown example (examples/sundown/): its spec compiles, and its builder writes it."""

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from chartwright.compiler import compile_bundle
from chartwright.spec import load_spec
from chartwright.testing import stub_resolution

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sundown"
PALETTES = sorted(p.stem for p in (EXAMPLE / "palettes").glob("*.json"))


def _build(palette: str | None = None) -> str:
    args = [sys.executable, str(EXAMPLE / "build_spec_sundown.py"), str(EXAMPLE / "facts.json")]
    if palette:
        args.append(palette)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run(args, capture_output=True, check=True, env=env).stdout
    return out.decode("utf-8").replace("\r\n", "\n")


def test_the_shipped_spec_compiles():
    spec = load_spec(json.loads((EXAMPLE / "sundown.json").read_text(encoding="utf-8")))
    bundle = zipfile.ZipFile(io.BytesIO(compile_bundle(spec, stub_resolution(spec))))
    assert sum("/charts/" in n for n in bundle.namelist()) == len(spec.charts)


def test_the_builder_writes_the_shipped_spec():
    shipped = (EXAMPLE / "sundown.json").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert _build() == shipped


@pytest.mark.parametrize("palette", PALETTES)
def test_every_palette_builds_a_valid_spec(palette):
    spec = load_spec(json.loads(_build(palette)))
    assert spec.dashboard.title == "Sundown"
