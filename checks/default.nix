{
  lib,
  pkgs,
  repositoryRoot,
}:
let
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
in
{
  kodi-capture = pkgs.runCommand "kodi-capture-tests" {
    nativeBuildInputs = [ pkgs.python3 ];
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
}
