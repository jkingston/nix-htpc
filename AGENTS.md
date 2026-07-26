# NixOS HTPC Project

## Deployment

Build the Raspberry Pi image:

```bash
nix build .#nixosConfigurations.htpc-pi.config.system.build.sdImage
```

After copying or cloning the repo onto an already-installed Pi:

```bash
sudo nixos-rebuild switch --flake /path/to/nix-htpc#htpc-pi
```

## Architecture

- **htpc-pi**: Raspberry Pi 4B Kodi appliance

## Key Services

- **Kodi**: Runs via greetd as a standalone GBM session (no X11/Wayland)
- **Jellyfin**: Kodi add-on only; this flake does not run a Jellyfin server
- **CEC**: TV remote control through Raspberry Pi HDMI0 native CEC
