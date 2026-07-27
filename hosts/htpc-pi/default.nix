{
  config,
  inputs,
  pkgs,
  ...
}:
{
  networking.hostName = "htpc-pi";

  # Mesa 26.1.5 crashes in v3d_write_uniforms when Kodi's DRM PRIME GLES
  # renderer draws decoded video on the Pi 4.
  hardware.graphics.package = inputs.mesa-nixpkgs.legacyPackages.aarch64-linux.mesa;

  # Kodi 21.3's FFmpeg patches predate the rpi_hevc_dec API in newer Pi
  # kernels. Use the last pre-regression Pi 4 kernel available from the
  # official NixOS binary cache until Kodi moves to the updated FFmpeg code.
  # https://github.com/raspberrypi/linux/issues/7228
  boot.kernelPackages =
    inputs.rpi-kernel-nixpkgs.legacyPackages.aarch64-linux.linuxPackages_rpi4;

  # Boot the kernel directly from the Pi firmware. U-Boot relocates the NixOS
  # initrd to the top of low memory, leaving at most 432 MiB for CMA. The
  # direct-kernel bootloader uses `followkernel`, leaving enough room for the
  # 512 MiB CMA pool required by full-frame 2160p HEVC.
  boot.loader.raspberry-pi.bootloader = "kernel";

  # The Pi firmware must apply vc4-kms-v3d before Linux so native CEC remains
  # available. Flatten this kernel's Pi 4 DTB into the layout expected by the
  # generational firmware installer.
  hardware.deviceTree.dtbSource = pkgs.runCommand "htpc-pi-kernel-dtbs" { } ''
    mkdir -p "$out"
    cp \
      ${config.boot.kernelPackages.kernel}/dtbs/broadcom/bcm2711-rpi-4-b.dtb \
      "$out/bcm2711-rpi-4-b.dtb"
    cp -R ${config.boot.kernelPackages.kernel}/dtbs/overlays "$out/overlays"
  '';
  boot.loader.raspberry-pi.useGenerationDeviceTree = true;

  hardware.raspberry-pi.config.pi4.options = {
    # The connected TV currently advertises UHD at up to 30 Hz. Enabling
    # 4K60 also makes warm reboots unreliable on some Pi 4 revisions.
    hdmi_enable_4kp60 = {
      enable = true;
      value = 0;
    };
  };

  # Prefer HDMI0 for applications using PipeWire's default output.
  services.pipewire.wireplumber.extraConfig."51-htpc-hdmi" = {
    "monitor.alsa.rules" = [
      {
        matches = [
          {
            "node.name" = "~alsa_output.platform-fef00700.hdmi.*";
          }
        ];
        actions."update-props"."priority.session" = 1200;
      }
      {
        matches = [
          {
            "node.name" = "alsa_output.platform-fe00b840.mailbox.stereo-fallback";
          }
        ];
        actions."update-props"."priority.session" = 100;
      }
    ];
  };

  # Filesystem (from SD image, will be auto-resized on first boot)
  fileSystems."/" = {
    device = "/dev/disk/by-label/NIXOS_SD";
    fsType = "ext4";
  };

  fileSystems."/boot/firmware" = {
    device = "/dev/disk/by-label/FIRMWARE";
    fsType = "vfat";
    options = [
      "nofail"
      "x-systemd.automount"
    ];
  };

  # SSH for remote access
  services.openssh.enable = true;
  users.users.root.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFOMJ1q1j4JRhT/VCzWGrHhFmCp/u2Lit5BaauaqR4hE"
  ];
}
