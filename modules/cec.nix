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
  # standby, however, make Kodi active on the first wake/routing message from
  # the TV even if it restores a different input.
  systemd.services.cec-tv-wake = {
    description = "Activate Kodi when the TV wakes";
    wantedBy = [ "multi-user.target" ];
    requires = [ "greetd.service" ];
    after = [ "greetd.service" ];
    partOf = [ "greetd.service" ];

    path = with pkgs; [
      coreutils
      gnugrep
      netcat-openbsd
      v4l-utils
    ];

    script = ''
      armed=false
      if timeout 12 cec-ctl -d /dev/cec0 --show-topology 2>&1 \
        | grep -qi 'Power Status.*Standby'; then
        armed=true
        echo "TV is in standby; CEC source activation armed"
      fi

      stdbuf -oL cec-ctl -d /dev/cec0 --monitor --wall-clock 2>&1 \
        | while IFS= read -r line; do
          case "$line" in
            "Received from TV"*"STANDBY"*)
              armed=true
              echo "TV entered standby; CEC source activation armed"
              ;;
            *"ACTIVE_SOURCE"*|\
            *"IMAGE_VIEW_ON"*|\
            *"TEXT_VIEW_ON"*|\
            "Received from TV"*"REPORT_POWER_STATUS"*"on"*|\
            "Received from TV"*"REQUEST_ACTIVE_SOURCE"*|\
            "Received from TV"*"ROUTING_CHANGE"*|\
            "Received from TV"*"ROUTING_INFORMATION"*|\
            "Received from TV"*"SET_STREAM_PATH"*)
              if "$armed"; then
                echo "TV wake/routing detected; asking Kodi to become active"
                for _ in $(seq 1 30); do
                  if nc -z 127.0.0.1 9090; then
                    ${kodiCecActivate}/bin/kodi-cec-activate
                    armed=false
                    echo "Kodi CEC source activation sent"
                    break
                  fi
                  sleep 1
                done
              fi
              ;;
          esac
        done
    '';

    serviceConfig = {
      Restart = "always";
      RestartSec = 5;
    };
  };
}
