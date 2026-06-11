"""The Source interface: anything that can produce a list of candidate
repositories for a given day."""

from __future__ import annotations

import abc

from ..models import Repository


class Source(abc.ABC):
    """A trend source produces candidate repositories.

    Implementations should be side-effect free (no DB writes) and return raw
    candidates; filtering/scoring happens later in the ranking stage.
    """

    name: str = "base"

    @abc.abstractmethod
    def fetch_candidates(self) -> list[Repository]:
        """Return candidate repositories (may contain duplicates across queries)."""
        raise NotImplementedError
