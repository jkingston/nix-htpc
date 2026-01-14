"""Local artwork cache management."""

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote


@dataclass
class CachedArtwork:
    """Cached artwork paths for a game."""

    game_id: str
    steamgriddb_id: int | None
    grid: Path | None
    hero: Path | None
    logo: Path | None
    icon: Path | None


@dataclass
class ArtworkOptionMeta:
    """Metadata for a cached artwork option."""

    index: int
    url: str
    author: str | None
    score: int
    style: str | None


ART_TYPES = ("grid", "hero", "logo", "icon")


class ArtworkCache:
    """Manages local artwork cache.

    Cache structure:
        ~/.Emulation/.pier/artwork/
          {game_id_urlencoded}/
            metadata.json
            grid/
              selected.png
              0.png, 1.png, ...
            hero/
            logo/
            icon/
    """

    def __init__(self, pier_dir: Path):
        """Initialize the cache.

        Args:
            pier_dir: Path to pier data directory (~/.Emulation/.pier)
        """
        self.cache_dir = pier_dir / "artwork"

    def _safe_game_id(self, game_id: str) -> str:
        """Convert game_id to filesystem-safe name."""
        return quote(game_id, safe="")

    def _unsafe_game_id(self, safe_id: str) -> str:
        """Convert filesystem-safe name back to game_id."""
        return unquote(safe_id)

    def get_cache_path(self, game_id: str) -> Path:
        """Get cache directory for a game."""
        return self.cache_dir / self._safe_game_id(game_id)

    def has_selected(self, game_id: str) -> bool:
        """Check if game has any selected artwork."""
        path = self.get_cache_path(game_id)
        return any((path / art_type / "selected.png").exists() for art_type in ART_TYPES)

    def get_selected_artwork(self, game_id: str) -> CachedArtwork:
        """Get paths to selected artwork files."""
        path = self.get_cache_path(game_id)
        metadata = self.load_metadata(game_id)

        def get_selected_path(art_type: str) -> Path | None:
            selected = path / art_type / "selected.png"
            if selected.exists():
                return selected
            # Check for other extensions
            for ext in (".jpg", ".jpeg", ".ico"):
                alt = path / art_type / f"selected{ext}"
                if alt.exists():
                    return alt
            return None

        return CachedArtwork(
            game_id=game_id,
            steamgriddb_id=metadata.get("steamgriddb_id"),
            grid=get_selected_path("grid"),
            hero=get_selected_path("hero"),
            logo=get_selected_path("logo"),
            icon=get_selected_path("icon"),
        )

    def cache_option(
        self,
        game_id: str,
        art_type: str,
        index: int,
        image_bytes: bytes,
        meta: ArtworkOptionMeta | None = None,
    ) -> Path:
        """Cache an artwork option.

        Args:
            game_id: Game identifier
            art_type: Type of artwork (grid, hero, logo, icon)
            index: Option index
            image_bytes: Image data
            meta: Optional metadata for this option

        Returns:
            Path to cached file
        """
        if art_type not in ART_TYPES:
            raise ValueError(f"Invalid art_type: {art_type}")

        path = self.get_cache_path(game_id) / art_type
        path.mkdir(parents=True, exist_ok=True)

        # Detect format from magic bytes
        ext = ".png"
        if image_bytes[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif image_bytes[:4] == b"\x00\x00\x01\x00":
            ext = ".ico"

        file_path = path / f"{index}{ext}"
        file_path.write_bytes(image_bytes)

        # Update metadata if provided
        if meta:
            metadata = self.load_metadata(game_id)
            if "options" not in metadata:
                metadata["options"] = {}
            if art_type not in metadata["options"]:
                metadata["options"][art_type] = []

            # Update or add this option
            opt_data = {
                "index": meta.index,
                "url": meta.url,
                "author": meta.author,
                "score": meta.score,
                "style": meta.style,
            }
            # Find and update existing or append
            found = False
            for i, existing in enumerate(metadata["options"][art_type]):
                if existing.get("index") == meta.index:
                    metadata["options"][art_type][i] = opt_data
                    found = True
                    break
            if not found:
                metadata["options"][art_type].append(opt_data)

            self.save_metadata(game_id, metadata)

        return file_path

    def select_option(self, game_id: str, art_type: str, index: int) -> bool:
        """Mark an option as selected.

        Args:
            game_id: Game identifier
            art_type: Type of artwork
            index: Option index to select

        Returns:
            True if option was found and selected
        """
        if art_type not in ART_TYPES:
            raise ValueError(f"Invalid art_type: {art_type}")

        path = self.get_cache_path(game_id) / art_type

        # Find the source file with any extension
        source = None
        for ext in (".png", ".jpg", ".jpeg", ".ico"):
            candidate = path / f"{index}{ext}"
            if candidate.exists():
                source = candidate
                break

        if not source:
            return False

        # Copy to selected (preserving extension)
        dest = path / f"selected{source.suffix}"
        # Remove any existing selected files
        for ext in (".png", ".jpg", ".jpeg", ".ico"):
            old = path / f"selected{ext}"
            if old.exists():
                old.unlink()

        shutil.copy(source, dest)

        # Update metadata
        metadata = self.load_metadata(game_id)
        if "selected" not in metadata:
            metadata["selected"] = {}
        metadata["selected"][art_type] = index
        self.save_metadata(game_id, metadata)

        return True

    def get_options(self, game_id: str, art_type: str) -> list[Path]:
        """List cached options for an artwork type.

        Returns paths sorted by index.
        """
        if art_type not in ART_TYPES:
            raise ValueError(f"Invalid art_type: {art_type}")

        path = self.get_cache_path(game_id) / art_type
        if not path.exists():
            return []

        options = []
        for f in path.iterdir():
            if f.name.startswith("selected"):
                continue
            # Extract index from filename
            stem = f.stem
            if stem.isdigit():
                options.append((int(stem), f))

        options.sort(key=lambda x: x[0])
        return [p for _, p in options]

    def get_selected_index(self, game_id: str, art_type: str) -> int | None:
        """Get the currently selected option index for an artwork type."""
        metadata = self.load_metadata(game_id)
        return metadata.get("selected", {}).get(art_type)

    def get_option_meta(self, game_id: str, art_type: str, index: int) -> ArtworkOptionMeta | None:
        """Get metadata for a specific option."""
        metadata = self.load_metadata(game_id)
        options = metadata.get("options", {}).get(art_type, [])
        for opt in options:
            if opt.get("index") == index:
                return ArtworkOptionMeta(
                    index=opt["index"],
                    url=opt.get("url", ""),
                    author=opt.get("author"),
                    score=opt.get("score", 0),
                    style=opt.get("style"),
                )
        return None

    def load_metadata(self, game_id: str) -> dict:
        """Load metadata for a game."""
        path = self.get_cache_path(game_id) / "metadata.json"
        if not path.exists():
            return {"game_id": game_id}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"game_id": game_id}

    def save_metadata(self, game_id: str, data: dict) -> None:
        """Save metadata for a game."""
        path = self.get_cache_path(game_id)
        path.mkdir(parents=True, exist_ok=True)

        data["game_id"] = game_id
        data["last_updated"] = datetime.now().isoformat()

        meta_path = path / "metadata.json"
        meta_path.write_text(json.dumps(data, indent=2))

    def set_steamgriddb_id(self, game_id: str, sgdb_id: int) -> None:
        """Store the SteamGridDB game ID for future fetches."""
        metadata = self.load_metadata(game_id)
        metadata["steamgriddb_id"] = sgdb_id
        self.save_metadata(game_id, metadata)

    def get_steamgriddb_id(self, game_id: str) -> int | None:
        """Get the stored SteamGridDB game ID."""
        metadata = self.load_metadata(game_id)
        return metadata.get("steamgriddb_id")

    def clear_cache(self, game_id: str) -> None:
        """Clear all cached artwork for a game."""
        path = self.get_cache_path(game_id)
        if path.exists():
            shutil.rmtree(path)

    def list_cached_games(self) -> list[str]:
        """List all games with cached artwork."""
        if not self.cache_dir.exists():
            return []
        return [
            self._unsafe_game_id(d.name)
            for d in self.cache_dir.iterdir()
            if d.is_dir()
        ]
