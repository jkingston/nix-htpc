{ config, lib, ... }:
let
  inherit (lib) mkOption types;

  booleanDisabled = "0";
  booleanEnabled = "1";
  noCecDevice = "231";
  noAdvancedDeviceOverride = "";
  ignoreTvStandby = "36044";
  doubleTapTimeoutMs = "300";
  nativeButtonRepeatRateMs = "0";
  nativeButtonReleaseDelayMs = "0";

  settings = [
    {
      id = "enabled";
      value = booleanEnabled;
    }
    # Do not claim the source at startup or when the screensaver is dismissed.
    {
      id = "activate_source";
      value = booleanDisabled;
    }
    # Kodi's None choice is the enum plus an empty advanced override.
    {
      id = "standby_devices";
      value = noCecDevice;
    }
    {
      id = "standby_devices_advanced";
      value = noAdvancedDeviceOverride;
    }
    # Kodi exit and CEC adapter shutdown must not announce an inactive source.
    {
      id = "send_inactive_source";
      value = booleanDisabled;
    }
    # TV standby must neither suspend nor shut down the HTPC.
    {
      id = "standby_pc_on_tv_standby";
      value = ignoreTvStandby;
    }
    # Preserve Kodi's shutdown flag; the None/empty target pair above leaves
    # no CEC device to receive a standby command.
    {
      id = "standby_tv_on_pc_standby";
      value = booleanEnabled;
    }
    # Keep startup from waking either the TV or an AVR.
    {
      id = "wake_devices";
      value = noCecDevice;
    }
    {
      id = "wake_devices_advanced";
      value = noAdvancedDeviceOverride;
    }
    # Preserve the measured CEC remote cadence used by input classification.
    {
      id = "double_tap_timeout_ms";
      value = doubleTapTimeoutMs;
    }
    {
      id = "button_repeat_rate_ms";
      value = nativeButtonRepeatRateMs;
    }
    {
      id = "button_release_delay_ms";
      value = nativeButtonReleaseDelayMs;
    }
    # Losing the active input pauses playback without powering off the HTPC.
    {
      id = "pause_playback_on_deactivate";
      value = booleanEnabled;
    }
  ];
  renderXml =
    orderedSettings:
    "<settings>\n"
    + lib.concatMapStrings (
      setting:
      "  <setting id=\"${setting.id}\" value=\"${setting.value}\"/>\n"
    ) orderedSettings
    + "</settings>\n";
  byId = builtins.listToAttrs (
    map (setting: {
      name = setting.id;
      inherit (setting) value;
    }) settings
  );
  validatedSettings =
    assert builtins.length settings == 13;
    assert builtins.length (lib.unique (map (setting: setting.id) settings)) == 13;
    assert byId.activate_source == booleanDisabled;
    assert byId.wake_devices == noCecDevice;
    assert byId.wake_devices_advanced == noAdvancedDeviceOverride;
    assert byId.standby_devices == noCecDevice;
    assert byId.standby_devices_advanced == noAdvancedDeviceOverride;
    assert byId.send_inactive_source == booleanDisabled;
    assert byId.standby_pc_on_tv_standby == ignoreTvStandby;
    settings;
  policy = config.htpc.cec.capturePolicy.peripheralData;
in
{
  options.htpc.cec.capturePolicy.peripheralData = {
    homeRelativePath = mkOption {
      type = types.strMatching "\\.kodi/userdata/peripheral_data/[A-Za-z0-9_]+\\.xml";
      readOnly = true;
      internal = true;
      default = ".kodi/userdata/peripheral_data/cec_CEC_Adapter.xml";
      description = "Home-relative path of Kodi's managed CEC adapter settings.";
    };

    settings = mkOption {
      type = types.listOf (
        types.submodule {
          options = {
            id = mkOption {
              type = types.strMatching "[a-z][a-z0-9_]*";
            };
            value = mkOption {
              type = types.strMatching "[0-9]*";
            };
          };
        }
      );
      readOnly = true;
      internal = true;
      default = validatedSettings;
      description = "Ordered Kodi CEC adapter settings.";
    };

    xml = mkOption {
      type = types.lines;
      readOnly = true;
      internal = true;
      default = renderXml policy.settings;
      description = "Exact managed Kodi CEC adapter XML.";
    };

    source = mkOption {
      type = types.path;
      readOnly = true;
      internal = true;
      default = builtins.toFile "cec_CEC_Adapter.xml" policy.xml;
      description = "Store file containing the managed Kodi CEC adapter XML.";
    };
  };
}
