"""SutAdapter protocol — the one interface every SUT integration implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from evalops.models import Case, Metadata, Sut, SutOutput


class SutAdapter(ABC):
    """Strategy interface: one call per case, async, context-propagating.

    Design decisions:
    - Async-only. Evaluation runs are embarrassingly parallel and we always
      want the runner to do structured concurrency. Adapters that wrap a
      sync SDK should offload with `anyio.to_thread.run_sync`.
    - Takes a pre-built `Metadata` so the runner can propagate trace IDs
      into the SUT call. This is what makes the bad-case harvester work:
      every case produces a trace the harvester can look up.
    - Returns a uniform `SutOutput`. Adapters are responsible for mapping
      their native response format onto it — SUT-specific fields go into
      `SutOutput.raw`.
    """

    def __init__(self, sut: Sut) -> None:
        self.sut = sut

    @abstractmethod
    async def call(self, case: Case, metadata: Metadata) -> SutOutput:
        """Invoke the SUT for one case and return a normalized output."""

    async def aclose(self) -> None:
        """Release any shared resources (HTTP pools, gRPC channels, ...).

        Default no-op; adapters with pooled clients override this.
        """
        return None

    @property
    def name(self) -> str:
        return self.sut.name
