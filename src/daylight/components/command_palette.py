"""The command palette provider: every Daylight action, fuzzy-searchable (FR-503).

The registry is not written down here -- it comes from the app, which builds it
from the very same ``BINDINGS`` the keys come from. An action therefore cannot
be reachable by key and missing from the palette.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from textual.command import DiscoveryHit, Hit, Hits, Provider

__all__ = ["DaylightCommandProvider", "PaletteCommand"]


@dataclass(frozen=True)
class PaletteCommand:
    """One palette entry: a title to fuzzy-match, a description, and what to run."""

    name: str
    description: str
    run: Callable[[], None]


class DaylightCommandProvider(Provider):
    """Offers every action the app exposes, matched with Textual's fuzzy matcher."""

    async def search(self, query: str) -> Hits:
        """Yield the commands whose title fuzzily matches ``query``, best first.

        An empty query is "everything" rather than "nothing": Textual's matcher
        cannot score one, and the palette asks for the whole list whenever the
        user clears the input.
        """
        if not query.strip():
            async for hit in self.discover():
                yield hit
            return
        matcher = self.matcher(query)
        for command in self.app.palette_commands():
            score = matcher.match(command.name)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(command.name),
                    command.run,
                    help=command.description,
                )

    async def discover(self) -> Hits:
        """Yield every command, in registry order, for an empty palette query."""
        for command in self.app.palette_commands():
            yield DiscoveryHit(command.name, command.run, help=command.description)
