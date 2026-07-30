{ lib, nixos-raspberrypi, pkgs, ... }:
let
  rpiPackages = nixos-raspberrypi.packages.aarch64-linux;
  kodiSettingsAddonVersion = "2.1.6";
  kodiOsdReviewAddonVersion = "0.1.0";
  kodiScreenshotPath = "/tmp/kodi-screenshots";
  kodiScreenshotEvidence = import ./kodi-screenshot-evidence/package.nix {
    inherit lib pkgs;
    screenshotPath = kodiScreenshotPath;
  };
  bingieMod = import ./bingie {
    inherit pkgs;
    kodiPackages = rpiPackages.kodi-gbm.packages;
  };
  kodiSettingsAddon = rpiPackages.kodi-gbm.packages.buildKodiAddon {
    pname = "htpc-settings";
    namespace = "service.htpc.settings";
    version = kodiSettingsAddonVersion;
    src = ./kodi-settings-addon;
    nativeCheckInputs = [
      pkgs.buildPackages.libxml2
      pkgs.buildPackages.python3
    ];
    postPatch = ''
      substituteInPlace service.py \
        --replace-fail \
          ${lib.escapeShellArg "@HTPC_SCREENSHOT_PATH@"} \
          ${lib.escapeShellArg kodiScreenshotPath}
    '';
    doCheck = true;
    checkPhase = ''
      runHook preCheck
      PYTHONDONTWRITEBYTECODE=1 \
        python3 -B -m unittest discover -s . -p 'test_*.py'
      xmllint --noout \
        addon.xml \
        resources/skins/Default/1080i/ChapterRail.xml
      runHook postCheck
    '';
    postInstall = ''
      addon_dir="$out/share/kodi/addons/service.htpc.settings"
      for runtime_file in \
        service.py \
        seek_controller.py \
        player_adapter.py \
        input_router.py \
        input_quarantine.py \
        presenter.py \
        playback_view_model.py \
        media_contract.py \
        chapter_dialog.py \
        resources/skins/Default/1080i/ChapterRail.xml
      do
        test -f "$addon_dir/$runtime_file"
      done

      grep -Fq \
        'version="${kodiSettingsAddonVersion}"' \
        "$addon_dir/addon.xml"
      grep -Fq \
        'SCREENSHOT_PATH = "${kodiScreenshotPath}"' \
        "$addon_dir/service.py"
      if grep -Fq '@HTPC_SCREENSHOT_PATH@' "$addon_dir/service.py"; then
        echo "Managed screenshot path was not substituted" >&2
        exit 1
      fi

      if grep -R -n '\.setPosition(' --include='*.py' "$addon_dir" \
        || grep -n '\.getControl(' "$addon_dir/presenter.py"
      then
        echo "Presenter must not retain or mutate Kodi window controls" >&2
        exit 1
      fi
    '';
  };
  kodiOsdReviewAddon = rpiPackages.kodi-gbm.packages.buildKodiAddon {
    pname = "htpc-osd-review";
    namespace = "script.htpc.osd-review";
    version = kodiOsdReviewAddonVersion;
    src = ./kodi-osd-review;
    nativeCheckInputs = [
      pkgs.buildPackages.libxml2
      pkgs.buildPackages.python3
    ];
    doCheck = true;
    checkPhase = ''
      runHook preCheck
      export HTPC_OSD_REVIEW_WINDOW=${
        ./bingie/src/1080i/Custom_1192_HTPCVideoOSDReview.xml
      }
      export HTPC_OSD_REVIEW_SKIN_ROOT=${./bingie/src}
      PYTHONDONTWRITEBYTECODE=1 \
        python3 -B -m unittest discover -s . -p 'test_*.py'
      xmllint --noout addon.xml
      runHook postCheck
    '';
    postInstall = ''
      addon_dir="$out/share/kodi/addons/script.htpc.osd-review"
      for runtime_file in addon.xml default.py review_contract.py
      do
        test -f "$addon_dir/$runtime_file"
      done
      grep -Fq \
        'version="${kodiOsdReviewAddonVersion}"' \
        "$addon_dir/addon.xml"
      grep -Fq 'id="script.htpc.osd-review"' "$addon_dir/addon.xml"
      grep -Fq \
        'point="xbmc.python.script" library="default.py"' \
        "$addon_dir/addon.xml"
    '';
  };
  jellyfinHtpc = import ./jellyfin {
    kodiPackages = rpiPackages.kodi-gbm.packages;
  };
  kodiWithAddons = rpiPackages.kodi-gbm.withPackages (kodiPkgs: with kodiPkgs; [
    jellyfinHtpc
    kodiSettingsAddon
    kodiOsdReviewAddon
  ]);
in
{
  environment.systemPackages = [
    kodiScreenshotEvidence
  ];
  system.build.kodiScreenshotEvidence = kodiScreenshotEvidence;
  system.build.kodiOsdReviewAddon = kodiOsdReviewAddon;

  systemd.tmpfiles.rules = [
    "d ${kodiScreenshotPath} 0700 htpc users - -"
  ];

  # BINGIE must be writable because Skin Shortcuts generates an include inside
  # the skin directory. Install it from greetd's pre-start so Kodi is always
  # stopped while the staged, validated copy replaces managed skin files.
  systemd.services.greetd = {
    # The upstream greetd module protects interactive sessions from rebuild
    # restarts. This appliance deliberately restarts its sole Kodi session so
    # the pre-start skin sync and changed add-on closure take effect atomically.
    restartIfChanged = lib.mkForce true;

    path = [
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.libxml2
      pkgs.rsync
    ];

    preStart = ''
      set -eu

      source_skin=${bingieMod}/share/kodi/addons/skin.bingie
      addon_root=/home/htpc/.kodi/addons
      target_skin="$addon_root/skin.bingie"
      staged_skin="$addon_root/.skin.bingie.staged"

      install -d -m 0755 -o htpc -g users "$addon_root"
      install -d -m 0755 -o htpc -g users "$staged_skin"

      rsync \
        -a --checksum --delete --chmod=Du+rwx,Fu+rw \
        "$source_skin/" "$staged_skin/"

      test -f "$staged_skin/addon.xml"
      test -f "$staged_skin/1080i/Home.xml"
      xmllint --noout \
        "$staged_skin/addon.xml" \
        "$staged_skin/1080i/Home.xml" \
        "$staged_skin/1080i/IncludesOSD.xml"
      grep -q 'id="skin.bingie"' "$staged_skin/addon.xml"

      install -d -m 0755 -o htpc -g users "$target_skin"
      rsync \
        -a --checksum --delete --chmod=Du+rwx,Fu+rw \
        --exclude=/1080i/script-skinshortcuts-includes.xml \
        "$staged_skin/" "$target_skin/"
      chown -R htpc:users "$target_skin"
    '';

    restartTriggers = [
      bingieMod
      jellyfinHtpc
      kodiSettingsAddon
      kodiOsdReviewAddon
    ];
  };

  # Auto-login to Kodi via greetd.
  services.greetd = {
    enable = true;
    settings = {
      default_session = {
        command = "${kodiWithAddons}/bin/kodi-standalone";
        user = "htpc";
      };
    };
  };

  # Kodi disables newly discovered third-party add-ons by default. Enable all
  # managed add-ons once Kodi's local JSON-RPC endpoint is ready.
  systemd.services.kodi-settings = {
    description = "Enable managed Kodi add-ons";
    wantedBy = [ "multi-user.target" ];
    requires = [ "greetd.service" ];
    after = [ "greetd.service" ];
    partOf = [ "greetd.service" ];

    script = ''
      enable_managed_addon() {
        addon_id="$1"
        for ((attempt = 0; attempt < 60; attempt++)); do
          response="$(
            ${pkgs.coreutils}/bin/printf \
              '{"jsonrpc":"2.0","method":"Addons.SetAddonEnabled","params":{"addonid":"%s","enabled":true},"id":1}\n' \
              "$addon_id" \
              | ${pkgs.netcat-openbsd}/bin/nc -N -w 1 127.0.0.1 9090 \
              || true
          )"

          case "$response" in
            *'"result":"OK"'*) return 0 ;;
          esac

          sleep 1
        done

        echo "Kodi did not enable $addon_id within 60 seconds" >&2
        return 1
      }

      enable_managed_addon service.htpc.settings
      enable_managed_addon script.htpc.osd-review
    '';

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
  };

  # Keep the EventServer available for remote input and the local CEC helper.
  networking.firewall.allowedUDPPorts = [ 9777 ];

  # Disable console screen blanking.
  boot.kernelParams = [
    "consoleblank=0"
    # Full-frame 2160p HEVC exhausts smaller CMA pools and corrupts rpivid's
    # reference-frame queue. Direct kernel boot places the initrd low enough
    # for the Pi-recommended 512 MiB reservation to fit below the DMA limit.
    "cma=512M"
    # Keep the Kodi interface responsive at 1080p60. Kodi can still switch to
    # whitelisted UHD modes for playback through KMS.
    "video=HDMI-A-1:1920x1080@60"
  ];
}
