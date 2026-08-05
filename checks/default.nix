{
  htpcConfiguration,
  lib,
  pkgs,
  repositoryRoot,
}:
let
  cecPolicyEvaluation = lib.evalModules {
    modules = [ (repositoryRoot + "/modules/kodi-cec-policy.nix") ];
  };
  cecPeripheralData =
    cecPolicyEvaluation.config.htpc.cec.capturePolicy.peripheralData;
  cecPolicyXml = builtins.toFile "evaluated-cec_CEC_Adapter.xml" (
    cecPeripheralData.xml
  );
  cecPolicyGolden =
    repositoryRoot + "/checks/fixtures/cec_CEC_Adapter.xml";
  integratedCecPeripheralData =
    htpcConfiguration.htpc.cec.capturePolicy.peripheralData;
  integratedCecHomeFile =
    htpcConfiguration.home-manager.users.htpc.home.file.${
      integratedCecPeripheralData.homeRelativePath
    };
  cecWakeUnitPresent =
    htpcConfiguration.systemd.units ? "cec-tv-wake.service";
  settingsWatchdog =
    htpcConfiguration.systemd.services.kodi-settings-watchdog;
  kodiSettingsService =
    htpcConfiguration.systemd.services.kodi-settings;
  kodiSettingsServiceScriptText =
    builtins.unsafeDiscardStringContext kodiSettingsService.script;
  greetdRestartTriggers =
    htpcConfiguration.systemd.services.greetd.restartTriggers;
  kodiAddonReconciler =
    htpcConfiguration.system.build.kodiAddonReconciler;
  kodiBingieDependenciesCheck =
    htpcConfiguration.system.build.kodiBingieDependenciesCheck;
  kodiBingieHelper =
    htpcConfiguration.system.build.kodiBingieHelper;
  kodiBingieWidgets =
    htpcConfiguration.system.build.kodiBingieWidgets;
  kodiSimplejson =
    htpcConfiguration.system.build.kodiSimplejson;
  kodiWithAddons =
    htpcConfiguration.system.build.kodiWithAddons;
  kodiJellyfin = builtins.head kodiWithAddons.kodiAddonRoots;
  kodiSettingsAddon =
    htpcConfiguration.system.build.kodiSettingsAddon;
  kodiOsdReviewAddon =
    htpcConfiguration.system.build.kodiOsdReviewAddon;
  kodiCore = kodiWithAddons.kodiCore;
  expectedSimplejsonIdentity = {
    manifest_sha256 =
      "5f365075e7eb21c1b413dad78f2ef902c8d1c1d6168dd18c04483dbf9f31e1ca";
    version = "3.19.1+matrix.1";
  };
  expectedBingieHelperIdentity = {
    manifest_sha256 =
      "79ea0d00b20513105445bf6e16a0424ca816f77cf4cc26822dcd86874d83cdb6";
    version = "1.1.2";
  };
  expectedBingieWidgetsIdentity = {
    manifest_sha256 =
      "a89854ec8f370b3f04a52f180a8e502125da21e21e89e9db2d03864c9d5f345f";
    version = "1.0.2";
  };
  kodiAddonReconcilerConfiguration =
    kodiAddonReconciler.configuration;
  kodiAddonReconcilerCommand =
    "${kodiAddonReconciler}/bin/kodi-addon-reconciler";
  greetdPreStart =
    htpcConfiguration.systemd.services.greetd.preStart;
  greetdPreStartParts =
    lib.splitString
      (builtins.unsafeDiscardStringContext kodiAddonReconcilerCommand)
      (builtins.unsafeDiscardStringContext greetdPreStart);
  evidenceProducer =
    repositoryRoot
    + "/modules/kodi-screenshot-evidence/kodi_screenshot_evidence.py";
  passiveEvidenceProducer =
    repositoryRoot
    + "/modules/kodi-passive-evidence/kodi_passive_evidence.py";
  passiveEvidenceProducerTest =
    repositoryRoot
    + "/modules/kodi-passive-evidence/test_kodi_passive_evidence.py";
  captureSource = lib.fileset.toSource {
    root = repositoryRoot;
    fileset = repositoryRoot + "/tools/kodi_capture";
  };
  protocolSource = lib.fileset.toSource {
    root = repositoryRoot;
    fileset = lib.fileset.unions [
      (repositoryRoot + "/checks/test_screenshot_evidence_protocol.py")
      evidenceProducer
      (repositoryRoot + "/tools/kodi_capture")
    ];
  };
  passiveEvidenceProducerSource = lib.fileset.toSource {
    root = repositoryRoot;
    fileset = lib.fileset.unions [
      passiveEvidenceProducer
      passiveEvidenceProducerTest
    ];
  };
  passiveEvidenceProtocolSource = lib.fileset.toSource {
    root = repositoryRoot;
    fileset = lib.fileset.unions [
      (repositoryRoot
        + "/checks/test_passive_evidence_producer_protocol.py")
      passiveEvidenceProducer
      (repositoryRoot + "/tools/kodi_capture")
    ];
  };
  bingieDependencyAuditSource = lib.fileset.toSource {
    root = repositoryRoot;
    fileset = lib.fileset.unions [
      (repositoryRoot + "/checks/test_bingie_dependency_inventory.py")
      (repositoryRoot
        + "/modules/bingie/audit/dependency-inventory.json")
      (repositoryRoot + "/modules/bingie/default.nix")
      (lib.fileset.fileFilter
        (file: file.hasExt "xml" || file.hasExt "xsp")
        (repositoryRoot + "/modules/bingie/src"))
      (repositoryRoot + "/tools/bingie_dependency_inventory.py")
    ];
  };
  kodiAddonReconcilerSource = lib.fileset.toSource {
    root = repositoryRoot;
    fileset = lib.fileset.unions [
      (repositoryRoot + "/modules/kodi-addon-reconciler/main.py")
      (repositoryRoot + "/modules/kodi-addon-reconciler/reconciler.py")
      (repositoryRoot + "/modules/kodi-addon-reconciler/test_reconciler.py")
    ];
  };
  osdReviewSource = lib.fileset.toSource {
    root = repositoryRoot;
    fileset = lib.fileset.unions [
      (repositoryRoot + "/modules/kodi-osd-review")
      (repositoryRoot
        + "/modules/bingie/src/1080i/Custom_1192_HTPCVideoOSDReview.xml")
      (repositoryRoot + "/modules/bingie/src/resources/review")
    ];
  };
in
{
  kodi-settings-watchdog-contract =
    assert settingsWatchdog.requires == [ "greetd.service" ];
    assert builtins.elem "greetd.service" settingsWatchdog.after;
    assert builtins.elem "kodi-settings.service" settingsWatchdog.after;
    assert settingsWatchdog.partOf == [ "greetd.service" ];
    assert settingsWatchdog.serviceConfig.User == "htpc";
    assert settingsWatchdog.serviceConfig.Group == "users";
    assert settingsWatchdog.serviceConfig.Restart == "always";
    assert settingsWatchdog.serviceConfig.NoNewPrivileges == true;
    assert settingsWatchdog.serviceConfig.ProtectSystem == "strict";
    assert !(settingsWatchdog.serviceConfig ? PrivateNetwork);
    assert settingsWatchdog.serviceConfig.RestrictAddressFamilies
      == [ "AF_INET" "AF_INET6" ];
    pkgs.runCommand "kodi-settings-watchdog-contract" { } ''
      touch "$out"
    '';

  kodi-cec-policy =
    assert integratedCecPeripheralData.homeRelativePath
      == cecPeripheralData.homeRelativePath;
    assert integratedCecPeripheralData.homeRelativePath
      == ".kodi/userdata/peripheral_data/cec_CEC_Adapter.xml";
    assert integratedCecPeripheralData.settings == cecPeripheralData.settings;
    assert integratedCecPeripheralData.xml == cecPeripheralData.xml;
    assert integratedCecPeripheralData.source == cecPeripheralData.source;
    assert integratedCecHomeFile.source == integratedCecPeripheralData.source;
    assert integratedCecHomeFile.target
      == integratedCecPeripheralData.homeRelativePath;
    assert integratedCecHomeFile.enable == true;
    # Home Manager's null default preserves the source's non-executable mode.
    assert integratedCecHomeFile.executable == null;
    assert integratedCecHomeFile.force == false;
    assert integratedCecHomeFile.ignorelinks == false;
    assert integratedCecHomeFile.onChange == "";
    assert integratedCecHomeFile.recursive == false;
    assert integratedCecHomeFile.text == null;
    assert !cecWakeUnitPresent;
    pkgs.runCommand "kodi-cec-policy-check" { } ''
    expectedHomeRelativePath=".kodi/userdata/peripheral_data/cec_CEC_Adapter.xml"
    expectedByteCount=663
    expectedSha256=444cccc0d27fd7ea9e27809cf6d175c30c717f136b96e70b4e7da5dffb339217

    compareExact() {
      candidate="$1"
      label="$2"
      if ! ${pkgs.diffutils}/bin/cmp -s "$candidate" ${cecPolicyGolden}; then
        echo "$label differs from the managed CEC policy golden" >&2
        ${pkgs.diffutils}/bin/diff -u ${cecPolicyGolden} "$candidate" >&2 \
          || true
        return 1
      fi
    }

    compareExact ${cecPeripheralData.source} "policy source"
    compareExact ${cecPolicyXml} "policy XML"

    homeRelativePath=${lib.escapeShellArg cecPeripheralData.homeRelativePath}
    if [[ "$homeRelativePath" != "$expectedHomeRelativePath" ]]; then
      echo "managed CEC policy has unexpected Home-relative path:" >&2
      echo "  actual:   $homeRelativePath" >&2
      echo "  expected: $expectedHomeRelativePath" >&2
      exit 1
    fi

    byteCount="$(
      ${pkgs.coreutils}/bin/wc -c < ${cecPeripheralData.source}
    )"
    if [[ "$byteCount" -ne "$expectedByteCount" ]]; then
      echo "managed CEC policy is $byteCount bytes;" \
        "expected $expectedByteCount" >&2
      exit 1
    fi

    sha256="$(
      ${pkgs.coreutils}/bin/sha256sum ${cecPeripheralData.source} \
        | ${pkgs.coreutils}/bin/cut -d ' ' -f 1
    )"
    if [[ "$sha256" != "$expectedSha256" ]]; then
      echo "managed CEC policy has unexpected SHA-256: $sha256" >&2
      exit 1
    fi

    ${pkgs.libxml2}/bin/xmllint --nonet --noout \
      ${cecPeripheralData.source}
    touch "$out"
  '';

  kodi-capture = pkgs.runCommand "kodi-capture-tests" {
    nativeBuildInputs = [
      pkgs.openssh
      pkgs.python3
    ];
  } ''
    cd ${captureSource}
    export PYTHONDONTWRITEBYTECODE=1
    python3 -B -m unittest discover \
      -s tools/kodi_capture/tests \
      -p 'test_*.py' \
      -v
    touch "$out"
  '';

  kodi-screenshot-evidence-protocol =
    pkgs.runCommand "kodi-screenshot-evidence-protocol-test" {
      nativeBuildInputs = [ pkgs.python3 ];
    } ''
      cd ${protocolSource}
      export PYTHONDONTWRITEBYTECODE=1
      python3 -B checks/test_screenshot_evidence_protocol.py -v
      touch "$out"
    '';

  kodi-passive-evidence-producer =
    pkgs.runCommand "kodi-passive-evidence-producer-test" {
      nativeBuildInputs = [ pkgs.python3 ];
    } ''
      cd ${passiveEvidenceProducerSource}
      export PYTHONDONTWRITEBYTECODE=1
      python3 -B \
        modules/kodi-passive-evidence/test_kodi_passive_evidence.py \
        -v
      touch "$out"
    '';

  kodi-passive-evidence-protocol =
    pkgs.runCommand "kodi-passive-evidence-protocol-test" {
      nativeBuildInputs = [ pkgs.python3 ];
    } ''
      cd ${passiveEvidenceProtocolSource}
      export PYTHONDONTWRITEBYTECODE=1
      python3 -B checks/test_passive_evidence_producer_protocol.py -v
      touch "$out"
    '';

  bingie-dependency-audit = pkgs.runCommand "bingie-dependency-audit" {
    nativeBuildInputs = [ pkgs.python3 ];
  } ''
    cd ${bingieDependencyAuditSource}
    export PYTHONDONTWRITEBYTECODE=1
    python3 -B checks/test_bingie_dependency_inventory.py -v
    python3 -B tools/bingie_dependency_inventory.py check
    touch "$out"
  '';

  kodi-addon-reconciler =
    assert kodiAddonReconciler.activeRoot == "/home/htpc/.kodi/addons";
    assert kodiAddonReconciler.backupRoot
      == "/var/lib/nix-htpc/kodi-addon-backups";
    assert kodiAddonReconciler.managedAddons == [
      kodiSimplejson
      kodiBingieHelper
    ];
    assert kodiAddonReconcilerConfiguration.schema_version == 1;
    assert kodiAddonReconcilerConfiguration.backup_uid == 0;
    assert kodiAddonReconcilerConfiguration.backup_gid == 0;
    assert kodiAddonReconcilerConfiguration.backup_mode == 448;
    assert kodiAddonReconcilerConfiguration.specs == [
      {
        addon_id = "script.module.simplejson";
        managed = {
          addon_path =
            "${kodiSimplejson}/share/kodi/addons/script.module.simplejson";
          manifest_path =
            "${kodiSimplejson}/share/kodi/addons/script.module.simplejson/addon.xml";
          identity = expectedSimplejsonIdentity;
        };
        userdata = expectedSimplejsonIdentity;
      }
      {
        addon_id = "script.bingie.helper";
        managed = {
          addon_path =
            "${kodiBingieHelper}/share/kodi/addons/script.bingie.helper";
          manifest_path =
            "${kodiBingieHelper}/share/kodi/addons/script.bingie.helper/addon.xml";
          identity = expectedBingieHelperIdentity;
        };
        userdata = expectedBingieHelperIdentity;
      }
    ];
    assert builtins.length greetdPreStartParts == 2;
    assert !(lib.hasInfix "source_skin=" (builtins.head greetdPreStartParts));
    assert !(lib.hasInfix "rsync" (builtins.head greetdPreStartParts));
    assert lib.hasInfix "source_skin=" (builtins.elemAt greetdPreStartParts 1);
    assert lib.hasInfix "rsync" (builtins.elemAt greetdPreStartParts 1);
    assert builtins.elem
      "d /var/lib/nix-htpc 0755 root root - -"
      htpcConfiguration.systemd.tmpfiles.rules;
    assert builtins.elem
      "d /var/lib/nix-htpc/kodi-addon-backups 0700 root root - -"
      htpcConfiguration.systemd.tmpfiles.rules;
    pkgs.runCommand "kodi-addon-reconciler-tests" {
      nativeBuildInputs = [ pkgs.python3 ];
    } ''
      cd ${kodiAddonReconcilerSource}/modules/kodi-addon-reconciler
      export PYTHONDONTWRITEBYTECODE=1
      python3 -B -m unittest -v test_reconciler.py
      touch "$out"
    '';

  kodi-bingie-dependencies =
    assert kodiCore == kodiCore.passthru.kodi;
    assert kodiCore.packages.kodi == kodiCore;
    assert kodiSimplejson.version == "3.19.1+matrix.1";
    assert kodiSimplejson.namespace == "script.module.simplejson";
    assert kodiSimplejson.pythonPath == "lib";
    assert kodiBingieHelper.version == "1.1.2";
    assert kodiBingieHelper.namespace == "script.bingie.helper";
    assert kodiBingieHelper.manifestIdentity == {
      inherit (expectedBingieHelperIdentity) version;
      manifestSha256 =
        expectedBingieHelperIdentity.manifest_sha256;
    };
    assert kodiBingieHelper.provenance == {
      owner = "matke-84";
      repo = "script.bingie.helper";
      rev = "4599ecada369d823843bcf36cb55e9cd67db137a";
      sourceHash =
        "sha256-3FRQLYSUp4lNMBruMUP+sDFIN/pD20iariHHGxni7QQ=";
    };
    assert kodiBingieHelper.requiredKodiAddons == [ kodiSimplejson ];
    assert kodiBingieWidgets.version == "1.0.2";
    assert kodiBingieWidgets.namespace == "script.bingie.widgets";
    assert kodiBingieWidgets.manifestIdentity == {
      inherit (expectedBingieWidgetsIdentity) version;
      manifestSha256 =
        expectedBingieWidgetsIdentity.manifest_sha256;
    };
    assert map (addon: addon.namespace) kodiWithAddons.kodiAddonRoots == [
      "plugin.video.jellyfin"
      "script.bingie.widgets"
      "service.htpc.settings"
      "script.htpc.osd-review"
      "script.module.simplejson"
      "script.bingie.helper"
    ];
    assert builtins.elem kodiBingieHelper kodiWithAddons.kodiRuntimeAddons;
    assert builtins.elem kodiBingieWidgets kodiWithAddons.kodiRuntimeAddons;
    assert builtins.elem kodiSimplejson kodiWithAddons.kodiRuntimeAddons;
    assert kodiJellyfin.namespace == "plugin.video.jellyfin";
    assert builtins.elem
      kodiCore.pythonPackages.pillow
      kodiJellyfin.propagatedBuildInputs;
    assert !(builtins.elem
      "service.openelec.settings"
      (map (addon: addon.namespace) kodiWithAddons.kodiRuntimeAddons));
    assert !(builtins.elem
      "service.libreelec.settings"
      (map (addon: addon.namespace) kodiWithAddons.kodiRuntimeAddons));
    assert kodiSettingsAddon.namespace == "service.htpc.settings";
    assert kodiSettingsAddon.version == "2.1.21";
    assert kodiOsdReviewAddon.namespace == "script.htpc.osd-review";
    assert kodiOsdReviewAddon.version == "0.1.3";
    assert builtins.elem kodiSettingsAddon greetdRestartTriggers;
    assert builtins.elem kodiOsdReviewAddon greetdRestartTriggers;
    assert kodiWithAddons.managedAddonEnableSpecs == [
      {
        addonId = "script.module.simplejson";
        version = "3.19.1+matrix.1";
      }
      {
        addonId = "script.bingie.helper";
        version = "1.1.2";
      }
      {
        addonId = "script.bingie.widgets";
        version = "1.0.2";
      }
      {
        addonId = "service.htpc.settings";
        version = "2.1.21";
      }
      {
        addonId = "script.htpc.osd-review";
        version = "0.1.3";
      }
    ];
    assert lib.hasInfix (builtins.unsafeDiscardStringContext ''
      enable_managed_addon script.module.simplejson 3.19.1+matrix.1 ${kodiWithAddons}/share/kodi/addons/script.module.simplejson/
      enable_managed_addon script.bingie.helper 1.1.2 ${kodiWithAddons}/share/kodi/addons/script.bingie.helper/
      enable_managed_addon script.bingie.widgets 1.0.2 ${kodiWithAddons}/share/kodi/addons/script.bingie.widgets/
      enable_managed_addon service.htpc.settings 2.1.21 ${kodiWithAddons}/share/kodi/addons/service.htpc.settings/
      enable_managed_addon script.htpc.osd-review 0.1.3 ${kodiWithAddons}/share/kodi/addons/script.htpc.osd-review/
    '') kodiSettingsServiceScriptText;
    assert lib.hasInfix
      "\"properties\":[\"broken\",\"enabled\",\"installed\",\"path\",\"version\"]"
      kodiSettingsServiceScriptText;
    assert lib.hasInfix
      "and (.result.addon.addonid == $addon_id)"
      kodiSettingsServiceScriptText;
    assert lib.hasInfix
      "and (.result.addon.version == $expected_version)"
      kodiSettingsServiceScriptText;
    assert lib.hasInfix
      "and (.result.addon.enabled == true)"
      kodiSettingsServiceScriptText;
    assert lib.hasInfix
      "and (.result.addon.installed == true)"
      kodiSettingsServiceScriptText;
    assert lib.hasInfix
      "and (.result.addon.broken == false)"
      kodiSettingsServiceScriptText;
    assert lib.hasInfix
      "and (.result.addon.path == $expected_path)"
      kodiSettingsServiceScriptText;
    assert lib.hasInfix
      "/bin/env HTPC_TRICKPLAY_CACHE_ROOT=/run/htpc-trickplay "
      htpcConfiguration.services.greetd.settings.default_session.command;
    assert lib.hasSuffix
      "${kodiWithAddons}/bin/kodi-standalone"
      htpcConfiguration.services.greetd.settings.default_session.command;
    assert builtins.elem
      "d /run/htpc-trickplay 0700 htpc users - -"
      htpcConfiguration.systemd.tmpfiles.rules;
    assert lib.hasInfix
      "find /run/htpc-trickplay -mindepth 1 -delete"
      htpcConfiguration.systemd.services.greetd.preStart;
    if pkgs.stdenv.hostPlatform.isLinux then
      kodiBingieDependenciesCheck
    else
      pkgs.runCommand "kodi-bingie-dependencies-evaluation-check" { } ''
        touch "$out"
      '';

  kodi-osd-review = pkgs.runCommand "kodi-osd-review-tests" {
    nativeBuildInputs = [
      pkgs.libxml2
      pkgs.python3
    ];
  } ''
    cd ${osdReviewSource}/modules/kodi-osd-review
    export HTPC_OSD_REVIEW_WINDOW=${
      osdReviewSource
    }/modules/bingie/src/1080i/Custom_1192_HTPCVideoOSDReview.xml
    export HTPC_OSD_REVIEW_SKIN_ROOT=${
      osdReviewSource
    }/modules/bingie/src
    export PYTHONDONTWRITEBYTECODE=1
    python3 -B -m unittest discover -s . -p 'test_*.py' -v
    xmllint --noout addon.xml "$HTPC_OSD_REVIEW_WINDOW"
    touch "$out"
  '';
}
