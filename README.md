# NixOS HTPC

NixOS flake for a Raspberry Pi 4B Kodi appliance.

## Deploy Raspberry Pi

```bash
# Build SD image (requires Linux or binfmt)
nix build .#nixosConfigurations.htpc-pi.config.system.build.sdImage

# Flash to SD card
sudo dd if=result/sd-image/*.img of=/dev/diskX bs=4M status=progress
```

Use the Raspberry Pi 4 HDMI0 port, the micro-HDMI port nearest USB-C, for the main TV connection.

## Update

```bash
# Update Pi after copying or cloning this repo onto it
sudo nixos-rebuild switch --flake /path/to/nix-htpc#htpc-pi
```

## Features

- **Kodi GBM**: Direct GPU rendering for a dedicated TV UI
- **Kodi add-ons**: Jellyfin, inputstream-adaptive, SponsorBlock, and YouTube
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
