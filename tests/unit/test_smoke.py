"""Smoke tests: the package installs and every subpackage imports."""

import importlib

import pytest

import querymind

SUBPACKAGES = [
    "api",
    "agent",
    "llm",
    "db",
    "safety",
    "semantic",
    "insights",
    "scheduler",
    "cache",
    "auth",
]


def test_version_is_set() -> None:
    assert querymind.__version__ == "0.1.0"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    module = importlib.import_module(f"querymind.{name}")
    assert module.__doc__, f"querymind.{name} should have a docstring"
