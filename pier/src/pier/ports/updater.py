"""Port version checking and updates."""

from collections.abc import Callable

from pier.ports.github import GitHubClient, GitHubError
from pier.ports.scanner import InstalledPort


def check_for_update(
    installed: InstalledPort,
    github_token: str | None = None,
) -> str | None:
    """Check if a newer version is available for a port.

    Args:
        installed: The installed port to check.
        github_token: Optional GitHub token for API rate limits.

    Returns:
        The new version tag if update available, None otherwise.
    """
    if not installed.version:
        # Can't compare without current version
        return None

    try:
        with GitHubClient(token=github_token) as github:
            release = github.get_latest_release(installed.port.github_repo)
            latest_version = release.tag_name

            # Simple comparison - if different, update available
            if latest_version != installed.version:
                return latest_version

            return None

    except GitHubError:
        return None


def check_all_for_updates(
    installed_ports: list[InstalledPort],
    github_token: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Check all installed ports for updates.

    Args:
        installed_ports: List of installed ports to check.
        github_token: Optional GitHub token for API rate limits.
        progress_callback: Optional callback for progress updates.

    Returns:
        Dict mapping port ID to new version for ports with updates.
    """
    updates = {}

    try:
        with GitHubClient(token=github_token) as github:
            for installed in installed_ports:
                if progress_callback:
                    progress_callback(f"Checking {installed.port.name}...")

                if not installed.version:
                    continue

                try:
                    release = github.get_latest_release(installed.port.github_repo)
                    if release.tag_name != installed.version:
                        updates[installed.id] = release.tag_name
                except GitHubError:
                    continue

    except GitHubError:
        pass

    return updates
