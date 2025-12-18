# NixOS HTPC

NixOS flake for two HTPC systems:

| Host | Hardware | Role |
|------|----------|------|
| `htpc-server` | Beelink SER5 Pro (AMD Ryzen 7 5850U) | Jellyfin server + Kodi |
| `htpc-pi` | Raspberry Pi 4B | Kodi client |

## Deploy Mini PC

Boot the NixOS installer USB, then:

```bash
# On the mini PC - enable SSH
sudo systemctl start sshd
sudo passwd root
ip a  # note the IP
```

From your Mac:

```bash
cd /Users/jack/workspace/nix-htpc
git add -A

nix run github:nix-community/nixos-anywhere -- \
  --flake .#htpc-server \
  --generate-hardware-config nixos-generate-config ./hosts/htpc-server/hardware-configuration.nix \
  --target-host root@192.168.68.53
```

After reboot, access:
- Kodi: displays on connected TV
- Jellyfin: http://htpc-server.local:8096

## Deploy Raspberry Pi

```bash
# Build SD image (requires Linux or binfmt)
nix build .#nixosConfigurations.htpc-pi.config.system.build.sdImage

# Flash to SD card
sudo dd if=result/sd-image/*.img of=/dev/diskX bs=4M status=progress
```

## Update

```bash
# Update mini PC remotely
nixos-rebuild switch --flake .#htpc-server --target-host root@htpc-server.local

# Update Pi (SSH in first)
sudo nixos-rebuild switch --flake /path/to/nix-htpc#htpc-pi
```

## Features

- **Kodi GBM**: Direct GPU rendering, HDR support
- **Jellyfin**: Hardware transcoding via VAAPI
- **CEC**: TV remote control (native on Pi, needs adapter on mini PC)
- **mDNS**: Access via `htpc-server.local` and `htpc-pi.local`
- **Auto-login**: Boots directly to Kodi
- **Zram swap**: Quiet operation, no disk swap

## CEC Notes

- **Raspberry Pi**: Native CEC on HDMI port near USB-C
- **Mini PC**: Requires DP-to-HDMI adapter with CEC tunneling (e.g., Club3D CAC-1080) or Pulse-Eight USB adapter

## Media Storage

Label your USB drive as `MEDIA` and it will auto-mount at `/media`:

```bash
sudo e2label /dev/sdX1 MEDIA
```
