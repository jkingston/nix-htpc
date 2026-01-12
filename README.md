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

## Session Switching

The mini PC supports switching between Kodi and Steam Big Picture:

```
┌─────────────────────────────────────────────────────────────────┐
│                          greetd                                  │
│                     (session manager)                            │
│                            │                                     │
│              ┌─────────────┴─────────────┐                       │
│              ↓                           ↓                       │
│         ┌─────────┐               ┌─────────────┐                │
│         │  Kodi   │ ←──────────── │   Steam     │                │
│         │(default)│   [Exit]      │ (gamescope) │                │
│         └─────────┘               └─────────────┘                │
│              ↑                           ↑                       │
│     [Favourites menu]           [Controller connect]             │
│     [Controller connect]                                         │
└─────────────────────────────────────────────────────────────────┘
```

### Triggers

| Trigger | Action |
|---------|--------|
| Boot | Start Kodi |
| Kodi → Steam (Favourites) | Quit Kodi, start Steam |
| Game controller connects | Switch to Steam (if in Kodi) |
| Exit Steam Big Picture | Return to Kodi |

### How It Works

1. **Session file**: `/tmp/htpc-session-request` signals which session to start
2. **greetd**: Runs `sessionSelector` script on each session start
3. **sessionSelector**: Reads session file, starts Kodi (default) or Steam

## CEC (TV Remote Control)

### Hardware

- **Raspberry Pi**: Native CEC on HDMI port near USB-C
- **Mini PC**: Requires DP-to-HDMI adapter with CEC tunneling (e.g., Club3D CAC-1080) or Pulse-Eight USB adapter

### CEC Flow

| Event | TV Behavior | HTPC Behavior |
|-------|-------------|---------------|
| HTPC boots | Turns ON, switches input | Kodi becomes active source |
| Kodi → Steam | **Stays ON** | CEC wake sent |
| Steam → Kodi | **Stays ON** | CEC wake sent |
| TV remote navigation | - | Controls Kodi via CEC |
| TV powered off (remote) | Turns OFF | Kodi pauses playback |
| TV powered on (remote) | Turns ON | Display wakes |
| "Power Off" in Kodi menu | Turns OFF (standby) | HTPC shuts down |
| Screensaver activates | **Stays ON** | No CEC standby sent |

### Key Principle

**Session switching ≠ shutdown**. When switching Kodi ↔ Steam:
- TV stays on (no CEC standby signals)
- CEC wake sent on session start

When explicitly shutting down from Kodi:
- TV receives standby command

## Kodi Home Menu Customization

The mini PC uses **Arctic Zephyr Reloaded** skin with customizable home menu via `script.skinshortcuts`.

### Adding Steam/Games to Home Menu

1. In Kodi, go to **Settings → Skin Settings → Configure Shortcuts**
2. Select the menu item you want to customize (or add a new one)
3. For Steam shortcut:
   - **Label**: Steam / Games / whatever you prefer
   - **Action**: `System.Exec(/run/current-system/sw/bin/steam-launcher)`
4. Save and the shortcut appears on your home screen

### How It Works

- Arctic Zephyr Reloaded uses `script.skinshortcuts` add-on for menu customization
- Config stored in `~/.kodi/userdata/addon_data/script.skinshortcuts/`
- Changes are stored in Kodi's userdata, not managed by NixOS
- The `steam-launcher` script writes "steam" to `/tmp/htpc-session-request` and quits Kodi
- greetd detects Kodi exit and starts the next session based on the flag file

### Returning to Kodi from Steam

Steam library includes a "Kodi" app (added via Steam ROM Manager). Launching it:
- Writes "kodi" to `/tmp/htpc-session-request`
- Gracefully shuts down Steam
- greetd restarts with Kodi session

### Configuration

CEC settings are managed in `modules/htpc-home.nix`:
- Kodi CEC peripheral settings in `.kodi/userdata/peripheral_data/`
- `standby_devices=231` (None) - Don't send Standby on Kodi exit
- `send_inactive_source=0` - Don't send Inactive Source on exit
- `standby_tv_on_pc_standby=1` - Send Standby when system shuts down

Session scripts in `modules/kodi.nix` send CEC wake via [cec-ctl](https://wiki.archlinux.org/title/HDMI-CEC):
```bash
cec-ctl -d /dev/cec0 --image-view-on
```

## Game Controller Integration

### Supported Controllers

- 8BitDo Pro 2 (and other 8BitDo controllers)
- Xbox controllers
- PlayStation controllers (DualShock, DualSense)
- Nintendo Pro Controller

### Controller → Steam Auto-Switch

When a game controller connects via Bluetooth:
1. udev rule detects the controller
2. systemd service checks current session
3. If in Kodi: switches to Steam, wakes TV
4. If in Steam: controller just connects normally

This enables a "pick up controller and play" experience - no need to navigate menus.

### Configuration

- udev rule: `modules/gaming.nix`
- Controller switch service: `modules/gaming.nix`

## Media Storage

Label your USB drive as `MEDIA` and it will auto-mount at `/media`:

```bash
sudo e2label /dev/sdX1 MEDIA
```
