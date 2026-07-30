"""Immutable observations used by the Kodi capture client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


CURRENT_WINDOW_LABEL = "System.CurrentWindow"
CURRENT_CONTROL_LABEL = "System.CurrentControl"
CURRENT_CONTROL_ID_LABEL = "System.CurrentControlId"
SELECTED_LABEL = "ListItem.Label"
SELECTED_DB_TYPE = "ListItem.DBTYPE"
SELECTED_DB_ID = "ListItem.DBID"
SELECTED_FOLDER_PATH = "ListItem.FolderPath"
SELECTED_FILE_PATH = "ListItem.FileNameAndPath"
SELECTED_UNIQUE_ID = "ListItem.UniqueID"
SERVICE_READY_LABEL = "Window(Home).Property(htpc.service.ready)"

SCREENSAVER_ACTIVE = "System.ScreenSaverActive"
DPMS_ACTIVE = "System.DPMSActive"
HOME_ACTIVE = "Window.IsActive(Home)"
MODAL_PRESENT = "System.HasModalDialog"
MODAL_ACTIVE = "System.HasActiveModalDialog"
BUSY_VISIBLE = "Window.IsVisible(busydialog)"
BUSY_NO_CANCEL_VISIBLE = "Window.IsVisible(busydialognocancel)"
PLAYER_HAS_MEDIA = "Player.HasMedia"

STANDARD_READINESS_LABELS = tuple(
    sorted(
        (
            CURRENT_WINDOW_LABEL,
            CURRENT_CONTROL_LABEL,
            CURRENT_CONTROL_ID_LABEL,
            SELECTED_LABEL,
            SELECTED_DB_TYPE,
            SELECTED_DB_ID,
            SELECTED_FOLDER_PATH,
            SELECTED_FILE_PATH,
            SELECTED_UNIQUE_ID,
            SERVICE_READY_LABEL,
        )
    )
)
STANDARD_READINESS_BOOLEANS = tuple(
    sorted(
        (
            SCREENSAVER_ACTIVE,
            DPMS_ACTIVE,
            HOME_ACTIVE,
            MODAL_PRESENT,
            MODAL_ACTIVE,
            BUSY_VISIBLE,
            BUSY_NO_CANCEL_VISIBLE,
            PLAYER_HAS_MEDIA,
        )
    )
)


class HomeInvariantError(ValueError):
    """A Kodi observation is not safe to accept as stable Home."""

    def __init__(self, violations: tuple[str, ...]):
        self.violations = violations
        super().__init__("Home invariant failed: %s" % "; ".join(violations))


@dataclass(frozen=True)
class Window:
    window_id: int
    label: str

    def __post_init__(self) -> None:
        _require_int(self.window_id, "window_id", minimum=1)
        _require_str(self.label, "window label")


@dataclass(frozen=True)
class Control:
    control_id: Optional[int]
    label: str

    def __post_init__(self) -> None:
        if self.control_id is not None:
            _require_int(self.control_id, "control_id", minimum=1)
        _require_str(self.label, "control label")


@dataclass(frozen=True)
class SelectedItem:
    label: str
    db_type: str
    db_id: Optional[int]
    folder_path: str
    file_path: str
    unique_id: str

    def __post_init__(self) -> None:
        for name in (
            "label",
            "db_type",
            "folder_path",
            "file_path",
            "unique_id",
        ):
            _require_str(getattr(self, name), "selected item %s" % name)
        if self.db_id is not None:
            _require_int(self.db_id, "selected item db_id", minimum=1)
            if not self.db_type:
                raise ValueError(
                    "selected item db_type is required with db_id"
                )

    @property
    def strong_identity(self) -> Optional[tuple[str, ...]]:
        """Return the strongest non-localized selected-item identity."""

        if self.db_id is not None:
            return ("database", self.db_type, str(self.db_id))
        if self.unique_id:
            return ("unique", self.db_type, self.unique_id)
        if self.file_path:
            return ("file", self.file_path)
        if self.folder_path:
            return ("folder", self.folder_path)
        return None


@dataclass(frozen=True)
class ActivePlayer:
    player_id: int
    kind: str

    def __post_init__(self) -> None:
        _require_int(self.player_id, "player_id", minimum=0)
        if self.kind not in ("audio", "picture", "video"):
            raise ValueError("active player kind is invalid")


@dataclass(frozen=True)
class KodiObservation:
    window: Window
    control: Control
    selected_item: SelectedItem
    active_players: tuple[ActivePlayer, ...]
    skin_id: str
    skin_name: str
    fullscreen: bool
    readiness_labels: tuple[tuple[str, str], ...]
    readiness_booleans: tuple[tuple[str, bool], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.window, Window):
            raise TypeError("window must be a Window")
        if not isinstance(self.control, Control):
            raise TypeError("control must be a Control")
        if not isinstance(self.selected_item, SelectedItem):
            raise TypeError("selected_item must be a SelectedItem")
        _require_str(self.skin_id, "skin_id", nonempty=True)
        _require_str(self.skin_name, "skin_name")
        _require_bool(self.fullscreen, "fullscreen")
        _validate_players(self.active_players)
        _validate_pairs(
            self.readiness_labels,
            "readiness labels",
            str,
        )
        _validate_pairs(
            self.readiness_booleans,
            "readiness booleans",
            bool,
        )
        _require_keys(
            self.readiness_labels,
            STANDARD_READINESS_LABELS,
            "readiness labels",
        )
        _require_keys(
            self.readiness_booleans,
            STANDARD_READINESS_BOOLEANS,
            "readiness booleans",
        )

    def label_value(self, key: str) -> str:
        return _pair_value(self.readiness_labels, key)

    def boolean_value(self, key: str) -> bool:
        return _pair_value(self.readiness_booleans, key)

    @property
    def focus_evidence(self) -> tuple[Any, ...]:
        """Return comparison evidence unaffected by screensaver recovery."""

        focus_booleans = tuple(
            pair
            for pair in self.readiness_booleans
            if pair[0] not in (SCREENSAVER_ACTIVE, DPMS_ACTIVE)
        )
        return (
            self.window,
            self.control,
            self.selected_item,
            self.active_players,
            self.skin_id,
            self.skin_name,
            self.fullscreen,
            self.readiness_labels,
            focus_booleans,
        )


@dataclass(frozen=True)
class CaptureSettings:
    screenshot_path: str
    screensaver_mode: str
    screensaver_time: int
    display_off_minutes: int

    def __post_init__(self) -> None:
        _require_str(
            self.screenshot_path,
            "screenshot_path",
            nonempty=True,
        )
        _require_str(
            self.screensaver_mode,
            "screensaver_mode",
            nonempty=True,
        )
        _require_int(
            self.screensaver_time,
            "screensaver_time",
            minimum=0,
        )
        _require_int(
            self.display_off_minutes,
            "display_off_minutes",
            minimum=0,
        )


@dataclass(frozen=True)
class AddonVersion:
    addon_id: str
    version: str
    enabled: bool
    installed: bool
    addon_type: str

    def __post_init__(self) -> None:
        _require_str(self.addon_id, "addon_id", nonempty=True)
        _require_str(self.version, "version", nonempty=True)
        _require_bool(self.enabled, "enabled")
        _require_bool(self.installed, "installed")
        _require_str(self.addon_type, "addon_type", nonempty=True)


def validate_addon_versions(addons: tuple[AddonVersion, ...]) -> None:
    """Require a frozen, unique sequence in canonical add-on ID order."""

    if not isinstance(addons, tuple):
        raise TypeError("add-ons must be a tuple")
    if any(not isinstance(addon, AddonVersion) for addon in addons):
        raise TypeError("add-ons must contain AddonVersion values")
    addon_ids = tuple(addon.addon_id for addon in addons)
    if len(addon_ids) != len(set(addon_ids)):
        raise ValueError("add-ons contain duplicate IDs")
    if addon_ids != tuple(sorted(addon_ids)):
        raise ValueError("add-ons must be sorted by ID")


def validate_home_observation(observation: KodiObservation) -> None:
    """Require the passive invariants for an accepted Home observation."""

    if not isinstance(observation, KodiObservation):
        raise TypeError("observation must be a KodiObservation")

    violations = []
    if observation.window.window_id != 10000:
        violations.append("active window is not 10000")
    if not observation.boolean_value(HOME_ACTIVE):
        violations.append("Home is not active")
    if observation.skin_id != "skin.bingie":
        violations.append("skin is not skin.bingie")
    if observation.fullscreen:
        violations.append("GUI is fullscreen")
    if observation.active_players:
        violations.append("an active player exists")
    if observation.boolean_value(PLAYER_HAS_MEDIA):
        violations.append("Player.HasMedia is true")
    if observation.boolean_value(DPMS_ACTIVE):
        violations.append("DPMS is active")
    if observation.boolean_value(MODAL_PRESENT):
        violations.append("a modal dialog exists")
    if observation.boolean_value(MODAL_ACTIVE):
        violations.append("an active modal dialog exists")
    if observation.boolean_value(BUSY_VISIBLE):
        violations.append("the busy dialog is visible")
    if observation.boolean_value(BUSY_NO_CANCEL_VISIBLE):
        violations.append("the non-cancelable busy dialog is visible")
    if observation.label_value(SERVICE_READY_LABEL) != "true":
        violations.append("the managed service is not ready")
    if (
        observation.control.control_id is not None
        and observation.control.control_id <= 0
    ):
        violations.append("the focused control ID is not positive")
    if observation.selected_item.strong_identity is None:
        violations.append("the selected item has no strong identity")

    if violations:
        raise HomeInvariantError(tuple(violations))


def _require_str(value: Any, name: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, str):
        raise TypeError("%s must be a string" % name)
    if nonempty and not value:
        raise ValueError("%s must not be empty" % name)


def _require_bool(value: Any, name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError("%s must be a boolean" % name)


def _require_int(
    value: Any,
    name: str,
    *,
    minimum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s must be an integer" % name)
    if value < minimum:
        raise ValueError("%s must be at least %d" % (name, minimum))


def _validate_players(players: tuple[ActivePlayer, ...]) -> None:
    if not isinstance(players, tuple):
        raise TypeError("active_players must be a tuple")
    if any(not isinstance(player, ActivePlayer) for player in players):
        raise TypeError("active_players must contain ActivePlayer values")
    player_ids = tuple(player.player_id for player in players)
    if player_ids != tuple(sorted(player_ids)):
        raise ValueError("active_players must be sorted by player_id")
    if len(player_ids) != len(set(player_ids)):
        raise ValueError("active_players contain duplicate player IDs")


def _validate_pairs(
    pairs: tuple[tuple[str, Any], ...],
    name: str,
    value_type: type,
) -> None:
    if not isinstance(pairs, tuple):
        raise TypeError("%s must be a tuple" % name)
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise TypeError("%s must contain two-item tuples" % name)
        key, value = pair
        _require_str(key, "%s key" % name, nonempty=True)
        if value_type is bool:
            _require_bool(value, "%s value" % name)
        elif not isinstance(value, value_type):
            raise TypeError(
                "%s value must be a %s"
                % (name, value_type.__name__)
            )
    keys = tuple(pair[0] for pair in pairs)
    if len(keys) != len(set(keys)):
        raise ValueError("%s contain duplicate keys" % name)
    if keys != tuple(sorted(keys)):
        raise ValueError("%s must be sorted by key" % name)


def _require_keys(
    pairs: tuple[tuple[str, Any], ...],
    required: tuple[str, ...],
    name: str,
) -> None:
    keys = {pair[0] for pair in pairs}
    missing = tuple(key for key in required if key not in keys)
    if missing:
        raise ValueError(
            "%s are missing required keys: %s"
            % (name, ", ".join(missing))
        )


def _pair_value(pairs: tuple[tuple[str, Any], ...], key: str) -> Any:
    for candidate, value in pairs:
        if candidate == key:
            return value
    raise KeyError(key)
