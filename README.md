# NixOS HTPC

NixOS flake for a Raspberry Pi 4B Kodi appliance.

## Deploy Raspberry Pi

Build and flash the project SD image. It uses the Raspberry Pi vendor kernel,
generational direct-kernel boot, a 1 GiB firmware partition, and an
automatically expanding root partition.

Use the Raspberry Pi 4 HDMI0 port, the micro-HDMI port nearest USB-C, for the main TV connection.

See [PI_INSTALL.md](PI_INSTALL.md) for the Mac build and flashing steps.

## Update

```bash
# Update Pi after copying or cloning this repo onto it
sudo nixos-rebuild switch --accept-flake-config --flake /path/to/nix-htpc#htpc-pi
```

## Features

- **Kodi GBM**: Direct GPU rendering for a dedicated TV UI
- **Kodi add-ons**: Jellyfin and the managed HTPC settings service
- **CEC**: TV remote control through HDMI-CEC
- **mDNS**: Access via `htpc-pi.local`
- **Auto-login**: Boots directly to Kodi
- **Zram swap**: Quiet operation, no disk swap

## CEC

### Hardware

Raspberry Pi 4 has native CEC on HDMI0, the port nearest USB-C.

### Behavior

| Event | CEC / TV effect | HTPC effect |
| --- | --- | --- |
| HTPC boots or Kodi restarts | Kodi requests no TV/AVR wake and no active-source change | Starts Kodi; `cec-tv-wake.service` begins polling TV power status |
| Supported TV-remote CEC key input | Requests no power or source change | Kodi handles the corresponding navigation or playback action |
| TV powers off or leaves the Pi input | The TV controls its own power or input | Stays running; Kodi pauses only if libCEC reports source deactivation |
| Armed standby or transitional state returns to on | The service requests `CECActivateSource`; the TV may switch to the Pi input | Disarms after sending; remains armed if Kodi is unavailable |
| Dim screensaver activates | Kodi sends no CEC standby | Dims the Kodi interface locally |
| Dim screensaver is dismissed | Kodi sends no CEC wake or active-source announcement | Restores the Kodi interface locally |
| Kodi Power Off/Powerdown | No CEC standby target is configured; the TV may react to HDMI signal loss only | Powers off the HTPC |

The managed adapter policy is defined once in
`modules/kodi-cec-policy.nix` as
`htpc.cec.capturePolicy.peripheralData`. Home Manager links its immutable XML at
`.kodi/userdata/peripheral_data/cec_CEC_Adapter.xml`. A golden-byte check and an
integrated configuration check lock the path, XML, Home Manager semantics, and
standby-armed wake-service state machine. The policy ensures that:

- startup, restart, and screensaver dismissal neither wake devices nor claim
  the active source;
- Kodi exit neither announces an inactive source nor targets a device for
  standby;
- TV standby does not suspend or shut down the HTPC;
- source deactivation may pause playback; and
- the measured CEC remote cadence remains unchanged.

`cec-tv-wake.service` is the deliberate exception to the passive source
policy. It arms only after observing the TV in standby or a transitional power
state. If it later observes the TV on, it waits for Kodi's local EventServer,
sends `CECActivateSource`, and disarms. If Kodi is unavailable, it remains
armed and retries. Starting the service while the TV is already on does not
activate the source. Headless capture therefore requires passive CEC and
journal evidence showing that no source activation occurred during the capture.

## Jellyfin

Kodi includes the Jellyfin add-on, but this flake no longer runs a Jellyfin server. Configure the add-on from Kodi's UI on first boot and point it at your existing Jellyfin server.
