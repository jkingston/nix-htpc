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
  cecWakeUnit =
    htpcConfiguration.systemd.units."cec-tv-wake.service";
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
    assert cecWakeUnit.name == "cec-tv-wake.service";
    assert builtins.hashString "sha256" (
      builtins.unsafeDiscardStringContext cecWakeUnit.text
    ) == "b62a875c878d8d690ad7595d980a29b6fb2d0408d6359a2658dd24f72d724e1e";
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
