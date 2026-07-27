# Build And Flash The Raspberry Pi

The image uses the Raspberry Pi vendor kernel and a 1 GiB firmware partition.
The root partition expands to fill the SD card on first boot.
Use a 16 GB or larger SD card; the uncompressed image is 7.76 GiB.

Assumptions:

- Repo path on the Mac: `/Users/jack/workspace/nix-htpc`
- Linux builder key: `/Users/jack/workspace/nix-htpc/keys/builder_ed25519`

## Flash The Current Image

The verified image is:

```text
/Users/jack/workspace/nix-htpc/htpc-pi.img.zst
SHA-256: ee4a62b02bc49cbac0e5eb4c41e4f7f1f0389b0279a98529900cb058fb0fe65f
```

Insert the SD card and identify its whole-disk device:

```bash
diskutil list
diskutil unmountDisk /dev/diskN
```

Replace `diskN` with the SD card, not the Mac's internal disk:

```bash
cd /Users/jack/workspace/nix-htpc
zstdcat htpc-pi.img.zst | sudo dd of=/dev/rdiskN bs=8m
sync
diskutil eject /dev/diskN
```

Press `Ctrl-T` while `dd` is running to print progress on macOS.

## Build It Again

### 1. Start The Linux Builder

Run this on the Mac and leave it running:

```bash
cd /Users/jack/workspace/nix-htpc

KEYS=./keys nix run --impure --expr 'let pkgs = import (builtins.getFlake "github:NixOS/nixpkgs/nixos-26.05") { system = "aarch64-darwin"; }; in pkgs.darwin.linux-builder.override { modules = [ { virtualisation.cores = 8; virtualisation.darwin-builder.memorySize = 32768; virtualisation.darwin-builder.diskSize = 131072; } ]; }'
```

Wait until `linux-builder` accepts SSH connections.

### 2. Copy The Configuration

Run this in another terminal on the Mac:

```bash
cd /Users/jack/workspace/nix-htpc

rsync -av --progress \
  --exclude='.git' \
  --exclude='keys' \
  --exclude='nixos.qcow2' \
  --exclude='result' \
  --exclude='*.img.zst' \
  -e 'ssh -i keys/builder_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/tmp/nix-htpc-builder-known-hosts' \
  ./ builder@linux-builder:/home/builder/nix-htpc/
```

### 3. Build The SD Image

```bash
ssh -i keys/builder_ed25519 \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/tmp/nix-htpc-builder-known-hosts \
  builder@linux-builder \
  'cd /home/builder/nix-htpc && nix --extra-experimental-features "nix-command flakes" --accept-flake-config build .#nixosConfigurations.htpc-pi.config.system.build.sdImage --max-jobs 8 --cores 8 -L'
```

The signed `nixos-raspberrypi` cache supplies the Pi kernel and Kodi GBM. The
Linux VM only assembles the final image.

### 4. Copy And Verify The Image

```bash
rsync -av --progress \
  -e 'ssh -i keys/builder_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/tmp/nix-htpc-builder-known-hosts' \
  'builder@linux-builder:/home/builder/nix-htpc/result/sd-image/*.img.zst' \
  ./htpc-pi.img.zst

zstd -t htpc-pi.img.zst
shasum -a 256 htpc-pi.img.zst
```

## First Boot

Insert the card and power on the Pi. The root filesystem expands automatically.
The configured SSH key allows direct access:

```bash
ssh root@htpc-pi.local
```

Future configuration changes can be built on the Mac and copied to the running Pi
without reflashing.
