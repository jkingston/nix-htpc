"""Unified game model and operations.

This module provides a unified interface for managing games regardless of
whether they are ROMs or PC ports.
"""

from pier.games.model import (
    Game,
    GameSource,
    GameType,
    InstalledGame,
    SearchResult,
)
from pier.games.search import search_games

__all__ = [
    "Game",
    "GameSource",
    "GameType",
    "InstalledGame",
    "SearchResult",
    "search_games",
]
