{ lib, pkgs }:

let
  pythonShebang = "#!${pkgs.python3}/bin/python3 -I";
  cecCtl = "${pkgs.v4l-utils}/bin/cec-ctl";
  systemctl = "${pkgs.systemd}/bin/systemctl";
  journalctl = "${pkgs.systemd}/bin/journalctl";
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "kodi-passive-evidence";
  version = "1";
  src = ./.;
  strictDeps = true;

  nativeCheckInputs = [
    pkgs.buildPackages.python3
  ];

  dontBuild = true;
  doCheck = true;

  postPatch = ''
    substituteInPlace kodi_passive_evidence.py \
      --replace-fail \
        ${lib.escapeShellArg "#!/usr/bin/env python3"} \
        ${lib.escapeShellArg pythonShebang} \
      --replace-fail \
        ${lib.escapeShellArg "@CEC_CTL@"} \
        ${lib.escapeShellArg cecCtl} \
      --replace-fail \
        ${lib.escapeShellArg "@SYSTEMCTL@"} \
        ${lib.escapeShellArg systemctl} \
      --replace-fail \
        ${lib.escapeShellArg "@JOURNALCTL@"} \
        ${lib.escapeShellArg journalctl}
  '';

  checkPhase = ''
    runHook preCheck
    test -f test_kodi_passive_evidence.py
    PYTHONDONTWRITEBYTECODE=1 \
      python3 -I -B -m unittest discover -s . -p 'test_*.py'
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -D -m 0555 \
      kodi_passive_evidence.py \
      "$out/bin/kodi-passive-evidence"
    runHook postInstall
  '';

  postInstall = ''
    program="$out/bin/kodi-passive-evidence"

    test -x "$program"
    test "$(${pkgs.buildPackages.coreutils}/bin/stat -c %a "$program")" = 555
    test "$(${pkgs.buildPackages.coreutils}/bin/head -n 1 "$program")" = \
      ${lib.escapeShellArg pythonShebang}

    for required in \
      ${lib.escapeShellArg cecCtl} \
      ${lib.escapeShellArg systemctl} \
      ${lib.escapeShellArg journalctl} \
      ${lib.escapeShellArg "KODI-PASSIVE-EVIDENCE/1"}
    do
      ${pkgs.buildPackages.gnugrep}/bin/grep -Fq "$required" "$program"
    done

    if ${pkgs.buildPackages.gnugrep}/bin/grep -Eq \
      '@[A-Z][A-Z0-9_]*@|^#!/usr/bin/env ' \
      "$program"
    then
      echo "Passive evidence producer contains an unresolved runtime path" >&2
      exit 1
    fi
  '';
}
