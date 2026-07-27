{
  nixos-raspberrypi,
  pkgs,
  ...
}:
let
  rpiKodi = nixos-raspberrypi.packages.aarch64-linux.kodi-gbm;
  kodiCecActivate = pkgs.writeScriptBin "kodi-cec-activate" ''
    #!${pkgs.python3}/bin/python3
    import socket
    import sys

    sys.path.insert(0, "${rpiKodi}/${pkgs.python3.sitePackages}")

    from kodi.xbmcclient import ACTION_EXECBUILTIN, PacketACTION

    address = ("127.0.0.1", 9777)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    PacketACTION(
        actionmessage="CECActivateSource",
        actiontype=ACTION_EXECBUILTIN,
    ).send(sock, address, uid=0)
  '';
in
{
  # libcec for CEC support
  environment.systemPackages = with pkgs; [
    libcec
    v4l-utils
  ];

  # udev rules for CEC device access
  services.udev.extraRules = ''
    # Raspberry Pi vchiq (for native CEC)
    KERNEL=="vchiq", GROUP="video", MODE="0660"
    # CEC devices
    KERNEL=="cec[0-9]*", GROUP="video", MODE="0660"
    # Pulse-Eight USB adapter
    SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", ATTRS{idVendor}=="2548", GROUP="dialout", MODE="0660"
  '';

  # Ensure htpc user can access CEC devices
  users.users.htpc.extraGroups = [ "video" "dialout" ];

  # Kodi must not wake or steal the TV during startup. Once the TV has entered
  # standby, however, make Kodi active when its CEC power status returns to on,
  # even if the TV restores a different input. Samsung TVs do not consistently
  # broadcast a routing message when woken with their own remote.
  systemd.services.cec-tv-wake = {
    description = "Activate Kodi when the TV wakes";
    wantedBy = [ "multi-user.target" ];
    requires = [ "greetd.service" ];
    after = [ "greetd.service" ];
    partOf = [ "greetd.service" ];

    path = with pkgs; [
      coreutils
      iproute2
      v4l-utils
    ];

    script = ''
      armed=false
      last_status=unknown

      while true; do
        response="$(
          timeout 3 cec-ctl -d /dev/cec0 --to 0 \
            --give-device-power-status 2>&1 || true
        )"

        case "$response" in
          # This Samsung reports 0x02 continuously while its screen is off
          # rather than settling at the CEC-defined standby state 0x01.
          *"pwr-state:"*"(0x01)"*|\
          *"pwr-state:"*"(0x02)"*|\
          *"pwr-state:"*"(0x03)"*)
            status=standby
            ;;
          *"pwr-state:"*"(0x00)"*)
            status=on
            ;;
          *)
            status=unknown
            ;;
        esac

        if [[ "$status" == standby ]]; then
          if [[ "$armed" == false ]]; then
            echo "TV is in standby; CEC source activation armed"
          fi
          armed=true
        elif [[ "$status" == on && "$armed" == true ]]; then
          echo "TV wake detected; asking Kodi to become active"
          for _ in $(seq 1 30); do
            if [[ -n "$(ss -H -lun 'sport = :9777')" ]]; then
              ${kodiCecActivate}/bin/kodi-cec-activate
              armed=false
              echo "Kodi CEC source activation sent"
              break
            fi
            sleep 1
          done

          if [[ "$armed" == true ]]; then
            echo "Kodi is unavailable; CEC source activation remains armed"
          fi
        fi

        if [[ "$status" != unknown && "$status" != "$last_status" ]]; then
          echo "TV power status: $status"
        fi
        last_status="$status"
        sleep 2
      done
    '';

    serviceConfig = {
      Restart = "always";
      RestartSec = 5;
    };
  };
}
