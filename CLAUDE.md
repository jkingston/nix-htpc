# NixOS HTPC Project

## Deployment

To deploy changes to the HTPC server:

```bash
# 1. Rsync files to server
rsync -avz --exclude='.git' /Users/jack/workspace/nix-htpc/ root@htpc-server.local:/etc/nixos/

# 2. Rebuild on server
ssh root@htpc-server.local 'cd /etc/nixos && nixos-rebuild switch --flake .#htpc-server'
```

## Architecture

- **htpc-server**: Beelink SER5 Pro (AMD Ryzen 5 5560U) running Kodi + Jellyfin
- **htpc-pi**: Raspberry Pi 4B (alternative frontend)

## Key Services

- **Kodi**: Runs via greetd as a standalone GBM session (no X11/Wayland)
- **Jellyfin**: Runs in Podman container on port 8096
- **AMD FPS Fix Monitor**: Systemd service that detects and fixes the MALL idle optimization bug

## Known Issues

- AMD MALL bug causes ~5fps drop in Kodi GBM. The `amd-fps-fix-monitor` service detects this and applies a DRM debug workaround.
- Kernel parameter `amdgpu.dcdebugmask` does NOT fix this issue on this hardware.
