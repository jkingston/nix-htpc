"""Strict read-only Kodi observations for capture readiness."""

from __future__ import annotations

from typing import Any, Optional

from .jsonrpc import JsonRpcClient, JsonRpcProtocolError
from .model import (
    CURRENT_CONTROL_ID_LABEL,
    CURRENT_CONTROL_LABEL,
    CURRENT_WINDOW_LABEL,
    SELECTED_DB_ID,
    SELECTED_DB_TYPE,
    SELECTED_FILE_PATH,
    SELECTED_FOLDER_PATH,
    SELECTED_LABEL,
    SELECTED_UNIQUE_ID,
    STANDARD_READINESS_BOOLEANS,
    STANDARD_READINESS_LABELS,
    ActivePlayer,
    AddonVersion,
    CaptureSettings,
    Control,
    KodiObservation,
    SelectedItem,
    Window,
    validate_addon_versions,
    validate_home_observation,
)


GUI_PROPERTIES = (
    "currentwindow",
    "currentcontrol",
    "skin",
    "fullscreen",
)
FOCUS_FENCE_LABELS = tuple(
    sorted(
        (
            CURRENT_WINDOW_LABEL,
            CURRENT_CONTROL_LABEL,
            CURRENT_CONTROL_ID_LABEL,
        )
    )
)
CAPTURE_SETTING_IDS = (
    "debug.screenshotpath",
    "screensaver.mode",
    "screensaver.time",
    "powermanagement.displaysoff",
)
CAPTURE_ADDON_IDS = (
    "plugin.video.jellyfin",
    "service.htpc.settings",
    "skin.bingie",
)


class KodiReadError(JsonRpcProtocolError):
    """Kodi returned a malformed or internally inconsistent observation."""


class KodiReadClient:
    """Expose only the passive Kodi reads required by capture."""

    def __init__(self, rpc: JsonRpcClient):
        self._rpc = rpc

    def observe_home(
        self,
        deadline: float,
        extra_readiness_labels: tuple[str, ...] = (),
        extra_readiness_booleans: tuple[str, ...] = (),
    ) -> KodiObservation:
        label_keys = _merge_readiness_keys(
            STANDARD_READINESS_LABELS,
            extra_readiness_labels,
            "extra readiness labels",
        )
        boolean_keys = _merge_readiness_keys(
            STANDARD_READINESS_BOOLEANS,
            extra_readiness_booleans,
            "extra readiness booleans",
        )

        before = self._read_focus_fence(deadline)
        players = self._read_active_players(deadline)
        booleans = self._read_info_booleans(boolean_keys, deadline)
        labels = self._read_info_labels(label_keys, deadline)
        after = self._read_focus_fence(deadline)
        if before != after:
            raise KodiReadError(
                "Kodi focus changed during the composite observation"
            )

        window, control, skin_id, skin_name, fullscreen = before
        if labels[CURRENT_WINDOW_LABEL] != window.label:
            raise KodiReadError(
                "middle and fenced current windows do not match"
            )
        if labels[CURRENT_CONTROL_LABEL] != control.label:
            raise KodiReadError(
                "middle and fenced current controls do not match"
            )
        middle_control_id = _optional_positive_label(
            labels[CURRENT_CONTROL_ID_LABEL],
            CURRENT_CONTROL_ID_LABEL,
        )
        if middle_control_id != control.control_id:
            raise KodiReadError(
                "middle and fenced current control IDs do not match"
            )

        try:
            selected = SelectedItem(
                label=labels[SELECTED_LABEL],
                db_type=labels[SELECTED_DB_TYPE],
                db_id=_optional_positive_label(
                    labels[SELECTED_DB_ID],
                    SELECTED_DB_ID,
                ),
                folder_path=labels[SELECTED_FOLDER_PATH],
                file_path=labels[SELECTED_FILE_PATH],
                unique_id=labels[SELECTED_UNIQUE_ID],
            )
            observation = KodiObservation(
                window=window,
                control=control,
                selected_item=selected,
                active_players=players,
                skin_id=skin_id,
                skin_name=skin_name,
                fullscreen=fullscreen,
                readiness_labels=tuple(sorted(labels.items())),
                readiness_booleans=tuple(sorted(booleans.items())),
            )
        except (TypeError, ValueError) as error:
            raise KodiReadError(str(error)) from error
        validate_home_observation(observation)
        return observation

    def read_capture_settings(self, deadline: float) -> CaptureSettings:
        values = {}
        for setting in CAPTURE_SETTING_IDS:
            result = self._call(
                "Settings.GetSettingValue",
                {"setting": setting},
                deadline,
            )
            result = _required_dict(
                result,
                ("value",),
                "Settings.GetSettingValue result",
            )
            values[setting] = result["value"]

        screenshot_path = _string_value(
            values["debug.screenshotpath"],
            "debug.screenshotpath",
            nonempty=True,
        )
        screensaver_mode = _string_value(
            values["screensaver.mode"],
            "screensaver.mode",
            nonempty=True,
        )
        screensaver_time = _integer_value(
            values["screensaver.time"],
            "screensaver.time",
            minimum=0,
        )
        display_off = _integer_value(
            values["powermanagement.displaysoff"],
            "powermanagement.displaysoff",
            minimum=0,
        )
        return CaptureSettings(
            screenshot_path=screenshot_path,
            screensaver_mode=screensaver_mode,
            screensaver_time=screensaver_time,
            display_off_minutes=display_off,
        )

    def read_addons(self, deadline: float) -> tuple[AddonVersion, ...]:
        addons = []
        for addon_id in CAPTURE_ADDON_IDS:
            result = self._call(
                "Addons.GetAddonDetails",
                {
                    "addonid": addon_id,
                    "properties": ["version", "enabled", "installed"],
                },
                deadline,
            )
            addon = _addon_result(result)
            if addon["addonid"] != addon_id:
                raise KodiReadError(
                    "Addons.GetAddonDetails returned the wrong addon ID"
                )
            addons.append(
                AddonVersion(
                    addon_id=_string_value(
                        addon["addonid"],
                        "addonid",
                        nonempty=True,
                    ),
                    version=_string_value(
                        addon["version"],
                        "addon version",
                        nonempty=True,
                    ),
                    enabled=_boolean_value(
                        addon["enabled"],
                        "addon enabled",
                    ),
                    installed=_boolean_value(
                        addon["installed"],
                        "addon installed",
                    ),
                    addon_type=_string_value(
                        addon["type"],
                        "addon type",
                        nonempty=True,
                    ),
                )
            )

        result = tuple(addons)
        try:
            validate_addon_versions(result)
        except (TypeError, ValueError) as error:
            raise KodiReadError(str(error)) from error
        return result

    def _read_gui(
        self,
        deadline: float,
    ) -> tuple[Window, Control, str, str, bool]:
        result = self._call(
            "GUI.GetProperties",
            {"properties": list(GUI_PROPERTIES)},
            deadline,
        )
        result = _required_dict(
            result,
            GUI_PROPERTIES,
            "GUI.GetProperties result",
        )
        current_window = _required_dict(
            result["currentwindow"],
            ("id", "label"),
            "currentwindow",
        )
        current_control = _required_dict(
            result["currentcontrol"],
            ("label",),
            "currentcontrol",
        )
        skin = _required_dict(
            result["skin"],
            ("id", "name"),
            "skin",
        )
        window = Window(
            _integer_value(
                current_window["id"],
                "currentwindow id",
                minimum=1,
            ),
            _string_value(
                current_window["label"],
                "currentwindow label",
            ),
        )
        control = Control(
            None,
            _string_value(
                current_control["label"],
                "currentcontrol label",
            ),
        )
        return (
            window,
            control,
            _string_value(skin["id"], "skin id", nonempty=True),
            _string_value(skin["name"], "skin name"),
            _boolean_value(result["fullscreen"], "fullscreen"),
        )

    def _read_focus_fence(
        self,
        deadline: float,
    ) -> tuple[Window, Control, str, str, bool]:
        window, gui_control, skin_id, skin_name, fullscreen = self._read_gui(
            deadline
        )
        labels = self._read_info_labels(FOCUS_FENCE_LABELS, deadline)
        if labels[CURRENT_WINDOW_LABEL] != window.label:
            raise KodiReadError(
                "GUI and fenced current windows do not match"
            )
        if labels[CURRENT_CONTROL_LABEL] != gui_control.label:
            raise KodiReadError(
                "GUI and fenced current controls do not match"
            )
        control = Control(
            _optional_positive_label(
                labels[CURRENT_CONTROL_ID_LABEL],
                CURRENT_CONTROL_ID_LABEL,
            ),
            gui_control.label,
        )
        return window, control, skin_id, skin_name, fullscreen

    def _read_active_players(
        self,
        deadline: float,
    ) -> tuple[ActivePlayer, ...]:
        result = self._call(
            "Player.GetActivePlayers",
            {},
            deadline,
        )
        if not isinstance(result, list):
            raise KodiReadError(
                "Player.GetActivePlayers result must be an array"
            )
        players = []
        player_ids = set()
        for raw_player in result:
            raw_player = _required_dict(
                raw_player,
                ("playerid", "type"),
                "active player",
            )
            player_id = _integer_value(
                raw_player["playerid"],
                "active player ID",
                minimum=0,
            )
            if player_id in player_ids:
                raise KodiReadError(
                    "active players contain a duplicate player ID"
                )
            player_ids.add(player_id)
            try:
                players.append(
                    ActivePlayer(
                        player_id,
                        _string_value(
                            raw_player["type"],
                            "active player type",
                            nonempty=True,
                        ),
                    )
                )
            except (TypeError, ValueError) as error:
                raise KodiReadError(str(error)) from error
        return tuple(sorted(players, key=lambda player: player.player_id))

    def _read_info_booleans(
        self,
        keys: tuple[str, ...],
        deadline: float,
    ) -> dict[str, bool]:
        result = self._call(
            "XBMC.GetInfoBooleans",
            {"booleans": list(keys)},
            deadline,
        )
        result = _required_dict(
            result,
            keys,
            "XBMC.GetInfoBooleans result",
        )
        return {
            key: _boolean_value(result[key], "info boolean %s" % key)
            for key in keys
        }

    def _read_info_labels(
        self,
        keys: tuple[str, ...],
        deadline: float,
    ) -> dict[str, str]:
        result = self._call(
            "XBMC.GetInfoLabels",
            {"labels": list(keys)},
            deadline,
        )
        result = _required_dict(
            result,
            keys,
            "XBMC.GetInfoLabels result",
        )
        return {
            key: _string_value(result[key], "info label %s" % key)
            for key in keys
        }

    def _call(
        self,
        method: str,
        params: dict[str, Any],
        deadline: float,
    ) -> Any:
        return self._rpc.call(method, params, deadline=deadline)


def _merge_readiness_keys(
    standard: tuple[str, ...],
    extra: tuple[str, ...],
    name: str,
) -> tuple[str, ...]:
    if not isinstance(extra, tuple):
        raise TypeError("%s must be a tuple" % name)
    if any(not isinstance(key, str) or not key for key in extra):
        raise ValueError("%s must contain nonempty strings" % name)
    if len(extra) != len(set(extra)):
        raise ValueError("%s contain duplicate keys" % name)
    if extra != tuple(sorted(extra)):
        raise ValueError("%s must be sorted" % name)
    collisions = set(standard).intersection(extra)
    if collisions:
        raise ValueError(
            "%s duplicate standard keys: %s"
            % (name, ", ".join(sorted(collisions)))
        )
    return tuple(sorted(standard + extra))


def _required_dict(
    value: Any,
    keys: tuple[str, ...],
    name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise KodiReadError("%s must be an object" % name)
    missing = tuple(key for key in keys if key not in value)
    if missing:
        raise KodiReadError(
            "%s is missing required keys: %s"
            % (name, ", ".join(missing))
        )
    return {key: value[key] for key in keys}


def _addon_result(result: Any) -> dict[str, Any]:
    wrapper = _required_dict(
        result,
        ("addon",),
        "Addons.GetAddonDetails result",
    )
    return _required_dict(
        wrapper["addon"],
        ("addonid", "version", "enabled", "installed", "type"),
        "add-on details",
    )


def _string_value(
    value: Any,
    name: str,
    *,
    nonempty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise KodiReadError("%s must be a string" % name)
    if nonempty and not value:
        raise KodiReadError("%s must not be empty" % name)
    return value


def _boolean_value(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise KodiReadError("%s must be a boolean" % name)
    return value


def _integer_value(
    value: Any,
    name: str,
    *,
    minimum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KodiReadError("%s must be an integer" % name)
    if value < minimum:
        raise KodiReadError(
            "%s must be at least %d" % (name, minimum)
        )
    return value


def _optional_positive_label(value: str, name: str) -> Optional[int]:
    if value in ("", "0"):
        return None
    if not value.isascii() or not value.isdecimal():
        raise KodiReadError("%s must be empty or a positive integer" % name)
    number = int(value)
    if number <= 0:
        raise KodiReadError("%s must be empty or a positive integer" % name)
    return number
