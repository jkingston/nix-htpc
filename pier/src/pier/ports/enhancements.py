"""Enhancement (texture pack) download and installation."""

import fnmatch
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from pier.ports.github import GitHubClient, GitHubError
from pier.ports.registry import Enhancement, Port


class EnhancementError(Exception):
    """Error during enhancement download or installation."""

    pass


def get_installed_enhancements(port: Port, install_dir: Path) -> list[str]:
    """Get list of installed enhancement IDs for a port.

    Args:
        port: The port to check.
        install_dir: Port installation directory.

    Returns:
        List of enhancement IDs that are installed.
    """
    installed = []

    for enhancement in port.enhancements:
        subdir = install_dir / enhancement.install_subdir
        if not subdir.exists():
            continue

        # Check for files matching the asset pattern using fnmatch
        for file in subdir.iterdir():
            if file.is_file() and fnmatch.fnmatch(file.name, enhancement.asset_pattern):
                installed.append(enhancement.id)
                break

    return installed


def download_enhancement(
    port: Port,
    enhancement: Enhancement,
    install_dir: Path,
    github_token: str | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> Path:
    """Download and install an enhancement pack.

    Args:
        port: The port the enhancement is for.
        enhancement: The enhancement to download.
        install_dir: Port installation directory.
        github_token: Optional GitHub token for API rate limits.
        progress_callback: Optional callback(status, downloaded, total).

    Returns:
        Path to the installed enhancement file.

    Raises:
        EnhancementError: If download or installation fails.
    """
    if progress_callback:
        progress_callback(f"Fetching latest release from {enhancement.repo}...", 0, 0)

    try:
        with GitHubClient(token=github_token) as github:
            release = github.get_latest_release(enhancement.repo)

            # Find matching asset
            matching_asset = None
            for asset in release.assets:
                if fnmatch.fnmatch(asset.name, enhancement.asset_pattern):
                    matching_asset = asset
                    break

            if not matching_asset:
                msg = (
                    f"No asset matching '{enhancement.asset_pattern}' "
                    f"in release {release.tag_name}"
                )
                raise EnhancementError(msg)

            # Download to temp location
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                def download_progress(downloaded: int, total: int) -> None:
                    if progress_callback:
                        progress_callback(
                            f"Downloading {matching_asset.name}...",
                            downloaded,
                            total,
                        )

                downloaded_file = github.download_asset(
                    matching_asset,
                    temp_path,
                    download_progress,
                )

                # Install to port's enhancement directory
                dest_dir = install_dir / enhancement.install_subdir
                dest_dir.mkdir(parents=True, exist_ok=True)

                dest_path = dest_dir / downloaded_file.name
                shutil.move(str(downloaded_file), str(dest_path))

                if progress_callback:
                    progress_callback("Installed successfully.", 0, 0)

                return dest_path

    except GitHubError as e:
        raise EnhancementError(f"Failed to download enhancement: {e}") from e


def remove_enhancement(
    port: Port,
    enhancement: Enhancement,
    install_dir: Path,
) -> bool:
    """Remove an installed enhancement.

    Args:
        port: The port the enhancement is for.
        enhancement: The enhancement to remove.
        install_dir: Port installation directory.

    Returns:
        True if removed, False if not found.
    """
    subdir = install_dir / enhancement.install_subdir
    if not subdir.exists():
        return False

    # Find and remove files matching the asset pattern
    pattern = enhancement.asset_pattern
    removed = False

    for file in subdir.iterdir():
        if file.is_file() and fnmatch.fnmatch(file.name, pattern):
            file.unlink()
            removed = True

    return removed
