{ lib, nixos-raspberrypi, pkgs, ... }:
let
  rpiPackages = nixos-raspberrypi.packages.aarch64-linux;
  kodiBase = rpiPackages.kodi-gbm;
  kodiTcpServerRaceBackport =
    ./kodi/patches/7c574313-tcpserver-lock-connections.patch;
  kodiCore = kodiBase.overrideAttrs (oldAttrs: {
    patches = (oldAttrs.patches or [ ]) ++ [
      kodiTcpServerRaceBackport
    ];
  });
  kodiPackages = kodiCore.packages;
  kodiSettingsAddonVersion = "2.1.15";
  kodiOsdReviewAddonVersion = "0.1.2";
  kodiScreenshotPath = "/tmp/kodi-screenshots";
  simplejsonIdentity = {
    version = "3.19.1+matrix.1";
    manifestSha256 =
      "5f365075e7eb21c1b413dad78f2ef902c8d1c1d6168dd18c04483dbf9f31e1ca";
  };
  bingieHelperIdentity = {
    version = "1.1.2";
    manifestSha256 =
      "79ea0d00b20513105445bf6e16a0424ca816f77cf4cc26822dcd86874d83cdb6";
  };
  managedAddonEnableSpecs = [
    {
      addonId = "script.module.simplejson";
      version = simplejsonIdentity.version;
    }
    {
      addonId = "script.bingie.helper";
      version = bingieHelperIdentity.version;
    }
    {
      addonId = "service.htpc.settings";
      version = kodiSettingsAddonVersion;
    }
    {
      addonId = "script.htpc.osd-review";
      version = kodiOsdReviewAddonVersion;
    }
  ];
  kodiScreenshotEvidence = import ./kodi-screenshot-evidence/package.nix {
    inherit lib pkgs;
    screenshotPath = kodiScreenshotPath;
  };
  simplejson = kodiPackages.simplejson;
  bingieHelper = import ./bingie-helper {
    inherit kodiPackages lib pkgs;
  };
  kodiAddonReconciler = import ./kodi-addon-reconciler {
    inherit lib pkgs;
    addonSpecs = [
      {
        addonId = "script.module.simplejson";
        userdata = simplejsonIdentity;
        managed = {
          addon = simplejson;
          identity = simplejsonIdentity;
        };
      }
      {
        addonId = "script.bingie.helper";
        userdata = bingieHelperIdentity;
        managed = {
          addon = bingieHelper;
          identity = bingieHelperIdentity;
        };
      }
    ];
  };
  bingieMod = import ./bingie {
    inherit kodiPackages pkgs;
  };
  kodiSettingsAddon = kodiPackages.buildKodiAddon {
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
  kodiSettingsWatchdog = import ./kodi-settings-watchdog {
    inherit lib pkgs;
    addonVersion = kodiSettingsAddonVersion;
  };
  kodiOsdReviewAddon = kodiPackages.buildKodiAddon {
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
    inherit kodiPackages;
  };
  baseKodiAddonRoots = [
    jellyfinHtpc
    kodiSettingsAddon
    kodiOsdReviewAddon
  ];
  kodiAddonRoots =
    baseKodiAddonRoots ++ kodiAddonReconciler.managedAddons;
  kodiRuntimeAddons = kodiPackages.requiredKodiAddons kodiAddonRoots;
  kodiWithAddons =
    (kodiCore.withPackages (_: kodiAddonRoots)).overrideAttrs (oldAttrs: {
      passthru = (oldAttrs.passthru or { }) // {
        inherit
          kodiCore
          kodiTcpServerRaceBackport
          kodiAddonRoots
          kodiRuntimeAddons
          managedAddonEnableSpecs
          ;
      };
    });
  kodiBingieDependenciesCheck =
    assert kodiCore == kodiCore.passthru.kodi;
    assert kodiCore.packages.kodi == kodiCore;
    assert builtins.elem kodiTcpServerRaceBackport kodiCore.patches;
    assert simplejson.version == simplejsonIdentity.version;
    assert bingieHelper.manifestIdentity == bingieHelperIdentity;
    assert bingieHelper.requiredKodiAddons == [ simplejson ];
    assert kodiAddonReconciler.managedAddons == [
      simplejson
      bingieHelper
    ];
    assert lib.drop (builtins.length baseKodiAddonRoots) kodiAddonRoots
      == kodiAddonReconciler.managedAddons;
    assert builtins.elem bingieHelper kodiRuntimeAddons;
    assert builtins.elem simplejson kodiRuntimeAddons;
    pkgs.runCommand "kodi-bingie-dependencies-check" {
      nativeBuildInputs = [
        pkgs.coreutils
        pkgs.libxml2
      ];
    } ''
      simplejson_manifest=${
        simplejson
      }/share/kodi/addons/script.module.simplejson/addon.xml
      helper_manifest=${
        bingieHelper
      }/share/kodi/addons/script.bingie.helper/addon.xml

      check_manifest() {
        manifest="$1"
        addon_id="$2"
        version="$3"
        sha256="$4"
        test "$(sha256sum "$manifest" | cut -d ' ' -f 1)" = "$sha256"
        test "$(xmllint --xpath 'string(/addon/@id)' "$manifest")" = \
          "$addon_id"
        test "$(xmllint --xpath 'string(/addon/@version)' "$manifest")" = \
          "$version"
      }

      check_manifest \
        "$simplejson_manifest" \
        script.module.simplejson \
        ${lib.escapeShellArg simplejsonIdentity.version} \
        ${lib.escapeShellArg simplejsonIdentity.manifestSha256}
      check_manifest \
        "$helper_manifest" \
        script.bingie.helper \
        ${lib.escapeShellArg bingieHelperIdentity.version} \
        ${lib.escapeShellArg bingieHelperIdentity.manifestSha256}

      test -f "${
        kodiWithAddons
      }/share/kodi/addons/script.module.simplejson/addon.xml"
      test -f "${
        kodiWithAddons
      }/share/kodi/addons/script.bingie.helper/addon.xml"
      propagated_inputs=()
      while IFS= read -r propagated_line || test -n "$propagated_line"; do
        line_inputs=()
        IFS=$' \t\n' read -r -a line_inputs <<< "$propagated_line"
        propagated_inputs+=("''${line_inputs[@]}")
      done < ${bingieHelper}/nix-support/propagated-build-inputs
      test "''${#propagated_inputs[@]}" -eq 1
      test "''${propagated_inputs[0]}" = ${lib.escapeShellArg "${simplejson}"}
      grep -Fq \
        ${
          lib.escapeShellArg
            "${simplejson}/share/kodi/addons/script.module.simplejson/lib"
        } \
        ${kodiWithAddons}/bin/kodi
      grep -Fq \
        ${lib.escapeShellArg "${kodiCore}/bin/kodi"} \
        ${kodiWithAddons}/bin/kodi
      test "$(
        sha256sum ${kodiTcpServerRaceBackport} | cut -d ' ' -f 1
      )" = a9abbd265e7b4c024e4caa75b104c6d1cb08a94ad9843f1a086ee42205d5356b

      touch "$out"
    '';
in
{
  environment.systemPackages = [
    kodiScreenshotEvidence
    kodiSettingsWatchdog
  ];
  system.build.kodiScreenshotEvidence = kodiScreenshotEvidence;
  system.build.kodiAddonReconciler = kodiAddonReconciler;
  system.build.kodiBingieDependenciesCheck =
    kodiBingieDependenciesCheck;
  system.build.kodiBingieHelper = bingieHelper;
  system.build.kodiSimplejson = simplejson;
  system.build.kodiWithAddons = kodiWithAddons;
  system.build.kodiSettingsAddon = kodiSettingsAddon;
  system.build.kodiOsdReviewAddon = kodiOsdReviewAddon;
  system.build.kodiSettingsWatchdog = kodiSettingsWatchdog;

  systemd.tmpfiles.rules = [
    "d ${kodiScreenshotPath} 0700 htpc users - -"
    "d /var/lib/nix-htpc 0755 root root - -"
    "d ${kodiAddonReconciler.backupRoot} 0700 root root - -"
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

      ${kodiAddonReconciler}/bin/kodi-addon-reconciler

      source_skin=${bingieMod}/share/kodi/addons/skin.bingie
      addon_root=/home/htpc/.kodi/addons
      target_skin="$addon_root/skin.bingie"
      staged_skin="$addon_root/.skin.bingie.staged"

      install -d -m 0755 -o htpc -g users "$addon_root"
      install -d -m 0755 -o htpc -g users "$staged_skin"

      rsync \
        -a --checksum --delete --chmod=Du+rwx,Fu+rw \
        "$source_skin/" "$staged_skin/"

      for required_file in \
        addon.xml \
        1080i/Home.xml \
        1080i/DialogSeekBar.xml \
        1080i/Includes.xml \
        1080i/IncludesHTPCPlayback.xml \
        1080i/IncludesHTPCVideoOSD.xml \
        1080i/IncludesOSD.xml \
        1080i/VideoOSD.xml \
        1080i/Custom_1158_AutoCloseOSD.xml
      do
        test -f "$staged_skin/$required_file"
      done

      xmllint --noout \
        "$staged_skin/addon.xml" \
        "$staged_skin/1080i/Home.xml" \
        "$staged_skin/1080i/DialogSeekBar.xml" \
        "$staged_skin/1080i/Includes.xml" \
        "$staged_skin/1080i/IncludesHTPCPlayback.xml" \
        "$staged_skin/1080i/IncludesHTPCVideoOSD.xml" \
        "$staged_skin/1080i/IncludesOSD.xml" \
        "$staged_skin/1080i/VideoOSD.xml" \
        "$staged_skin/1080i/Custom_1158_AutoCloseOSD.xml"

      owned_consumer_count="$(
        xmllint \
          --xpath 'count(//include[@content="HTPCVideoOSD"])' \
          "$staged_skin/1080i/VideoOSD.xml"
      )"
      test "$owned_consumer_count" = 1
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
      kodiAddonReconciler
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
        expected_version="$2"
        expected_path="$3"
        for ((attempt = 0; attempt < 60; attempt++)); do
          set_response="$(
            ${pkgs.coreutils}/bin/printf \
              '{"jsonrpc":"2.0","method":"Addons.SetAddonEnabled","params":{"addonid":"%s","enabled":true},"id":1}\n' \
              "$addon_id" \
              | ${pkgs.netcat-openbsd}/bin/nc -N -w 1 127.0.0.1 9090 \
              || true
          )"

          if ${pkgs.coreutils}/bin/printf '%s' "$set_response" \
            | ${pkgs.jq}/bin/jq -e \
              '
                (type == "object")
                and (.jsonrpc == "2.0")
                and (.id == 1)
                and (has("error") | not)
                and (.result == "OK")
              ' >/dev/null
          then
            details_response="$(
              ${pkgs.coreutils}/bin/printf \
                '{"jsonrpc":"2.0","method":"Addons.GetAddonDetails","params":{"addonid":"%s","properties":["broken","enabled","installed","path","version"]},"id":2}\n' \
                "$addon_id" \
                | ${pkgs.netcat-openbsd}/bin/nc -N -w 1 127.0.0.1 9090 \
                || true
            )"

            if ${pkgs.coreutils}/bin/printf '%s' "$details_response" \
              | ${pkgs.jq}/bin/jq -e \
                --arg addon_id "$addon_id" \
                --arg expected_version "$expected_version" \
                --arg expected_path "$expected_path" \
                '
                  (type == "object")
                  and (.jsonrpc == "2.0")
                  and (.id == 2)
                  and (has("error") | not)
                  and (.result | type == "object")
                  and (.result.addon | type == "object")
                  and (.result.addon.addonid == $addon_id)
                  and (.result.addon.version == $expected_version)
                  and (.result.addon.enabled == true)
                  and (.result.addon.installed == true)
                  and (.result.addon.broken == false)
                  and (.result.addon.path == $expected_path)
                ' >/dev/null
            then
              return 0
            fi
          fi

          sleep 1
        done

        echo \
          "Kodi did not enable $addon_id $expected_version at $expected_path within 60 seconds" \
          >&2
        return 1
      }

      ${
        lib.concatMapStringsSep "\n" (
          spec:
          "enable_managed_addon ${
            lib.escapeShellArg spec.addonId
          } ${lib.escapeShellArg spec.version} ${
            lib.escapeShellArg
              "${kodiWithAddons}/share/kodi/addons/${spec.addonId}/"
          }"
        ) managedAddonEnableSpecs
      }
    '';

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
  };

  systemd.services.kodi-settings-watchdog = {
    description = "Recover the managed Kodi settings add-on";
    wantedBy = [ "multi-user.target" ];
    requires = [ "greetd.service" ];
    after = [
      "greetd.service"
      "kodi-settings.service"
    ];
    partOf = [ "greetd.service" ];

    serviceConfig = {
      Type = "simple";
      User = "htpc";
      Group = "users";
      ExecStart = "${kodiSettingsWatchdog}/bin/kodi-settings-watchdog";
      Restart = "always";
      RestartSec = 1;

      NoNewPrivileges = true;
      PrivateDevices = true;
      PrivateTmp = true;
      ProtectClock = true;
      ProtectControlGroups = true;
      ProtectHome = true;
      ProtectHostname = true;
      ProtectKernelLogs = true;
      ProtectKernelModules = true;
      ProtectKernelTunables = true;
      ProtectSystem = "strict";
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
      ];
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      CapabilityBoundingSet = "";
      UMask = "0077";
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
