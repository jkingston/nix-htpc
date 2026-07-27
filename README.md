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

| Event | TV Behavior | HTPC Behavior |
|-------|-------------|---------------|
| HTPC boots | Turns on, switches input | Kodi becomes active source |
| TV remote navigation | - | Controls Kodi via CEC |
| TV powered off with remote | Turns off | Kodi pauses playback |
| TV powered on with remote | Turns on | Display wakes |
| "Power Off" in Kodi menu | Turns off | HTPC shuts down |
| Screensaver activates | Stays on | No CEC standby sent |

CEC settings are managed in `modules/htpc-home.nix`:

- Kodi CEC peripheral settings in `.kodi/userdata/peripheral_data/`
- `standby_devices=231`: do not send standby on Kodi exit
- `send_inactive_source=0`: do not announce inactive source on exit
- `standby_tv_on_pc_standby=1`: send standby when the system shuts down

## Jellyfin

Kodi includes the Jellyfin add-on, but this flake no longer runs a Jellyfin server. Configure the add-on from Kodi's UI on first boot and point it at your existing Jellyfin server.
