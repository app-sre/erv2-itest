"""Unit test for the erv2-itest console-script entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

from erv2_itest import __main__ as main_module

if TYPE_CHECKING:
    import pytest


def test_main_invokes_the_typer_app(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(main_module, "app", lambda: calls.append("called"))

    main_module.main()

    assert calls == ["called"]
