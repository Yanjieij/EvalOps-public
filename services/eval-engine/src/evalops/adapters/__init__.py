"""SUT adapters — translate EvalOps's uniform `Case` into SUT-native calls.

The factory resolves a `Sut` descriptor to a concrete `SutAdapter` instance.
Registering a new adapter is two lines: implement `SutAdapter` and add it
to the `_REGISTRY` map.
"""

from __future__ import annotations

from evalops.models import Sut, SutKind

from .base import SutAdapter
from .mock import MockAdapter
from .reference import ReferenceAdapter

_REGISTRY: dict[SutKind, type[SutAdapter]] = {
    SutKind.MOCK: MockAdapter,
    SutKind.REFERENCE: ReferenceAdapter,
}


def build_adapter(sut: Sut) -> SutAdapter:
    """Instantiate the adapter implementation that matches ``sut.kind``."""
    try:
        cls = _REGISTRY[sut.kind]
    except KeyError as exc:
        raise ValueError(f"No adapter registered for SUT kind {sut.kind!r}") from exc
    return cls(sut)


__all__ = ["MockAdapter", "ReferenceAdapter", "SutAdapter", "build_adapter"]
