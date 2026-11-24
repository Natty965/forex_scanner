"""Milestone 0 checks: the environment is set up correctly. No market data here."""

import importlib
import sys

import pytest


def test_python_version():
    assert sys.version_info >= (3, 12)


def test_running_inside_virtual_environment():
    # In a venv, sys.prefix points to .venv while sys.base_prefix points to the main install.
    assert sys.prefix != sys.base_prefix, "Activate .venv before running tests"


@pytest.mark.parametrize("module", ["yfinance", "pandas", "dotenv", "rich"])
def test_dependency_importable(module):
    importlib.import_module(module)


@pytest.mark.parametrize(
    "module",
    [
        "forex_scanner",
        "forex_scanner.config",
        "forex_scanner.providers",
        "forex_scanner.storage",
        "forex_scanner.analysis",
        "forex_scanner.display",
    ],
)
def test_project_packages_importable(module):
    importlib.import_module(module)


def test_env_file_is_ignored_by_git():
    from forex_scanner.config import PROJECT_ROOT

    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore.splitlines()
