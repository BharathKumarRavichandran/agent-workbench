"""Agent adapter package with lazy public registry exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import AdapterRegistry

__all__ = ["AdapterRegistry", "build_registry"]


def __getattr__(name: str) -> object:
    if name in __all__:
        from .registry import AdapterRegistry, build_registry

        return {"AdapterRegistry": AdapterRegistry, "build_registry": build_registry}[name]
    raise AttributeError(name)
