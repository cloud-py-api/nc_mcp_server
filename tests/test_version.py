"""The package reports the version it was installed with."""

import tomllib
from importlib.metadata import version
from pathlib import Path

import nc_mcp_server


def test_version_comes_from_the_installed_package() -> None:
    assert nc_mcp_server.__version__ == version("nc-mcp-server")


def test_pyproject_and_installed_version_agree() -> None:
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert nc_mcp_server.__version__ == pyproject["project"]["version"]
