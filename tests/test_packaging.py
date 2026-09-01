"""Packaging invariants. They fail silently in consumers, so they are asserted here."""

import subprocess
import sys
from pathlib import Path

import pytest

PACKAGES = ("corpus", "corpus_directus")
ROOT = Path(__file__).resolve().parent.parent

_NO_PYTEST = """
import sys
from importlib.abc import MetaPathFinder


class Block(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "pytest" or name.startswith("pytest."):
            raise ImportError("pytest is not installed in production")


sys.meta_path.insert(0, Block())
import corpus
import corpus_directus
from corpus.testing import DEFAULT_SEED, FakeCorpus

FakeCorpus.from_seed(DEFAULT_SEED)
print("ok")
"""


@pytest.mark.unit
@pytest.mark.parametrize("package", PACKAGES)
def test_package_ships_a_py_typed_marker(package):
    """Without it, consumers silently lose every annotation this library has."""
    assert (ROOT / package / "py.typed").is_file()


@pytest.mark.integration
def test_the_fake_backend_does_not_require_pytest():
    """`backend="fake"` is a production arm (demos, e2e, CI without secrets),
    so importing it must not drag a test framework into the image."""
    result = subprocess.run(
        [sys.executable, "-c", _NO_PYTEST], capture_output=True, text=True, cwd=ROOT
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
