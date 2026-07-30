{ lib, pkgs, screenshotPath }:

pkgs.stdenvNoCC.mkDerivation {
  pname = "kodi-screenshot-evidence";
  version = "1";
  src = ./.;

  nativeCheckInputs = [
    pkgs.buildPackages.python3
  ];

  dontBuild = true;
  doCheck = true;

  postPatch = ''
    substituteInPlace kodi_screenshot_evidence.py \
      --replace-fail \
        ${lib.escapeShellArg "@KODI_SCREENSHOT_PATH@"} \
        ${lib.escapeShellArg screenshotPath} \
      --replace-fail \
        ${lib.escapeShellArg "#!/usr/bin/env python3"} \
        ${lib.escapeShellArg "#!${pkgs.python3}/bin/python3"}
  '';

  checkPhase = ''
    runHook preCheck
    PYTHONDONTWRITEBYTECODE=1 \
      python3 -B -m unittest discover -s . -p 'test_*.py'
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -D -m 0555 \
      kodi_screenshot_evidence.py \
      "$out/bin/kodi-screenshot-evidence"
    runHook postInstall
  '';

  postInstall = ''
    test -x "$out/bin/kodi-screenshot-evidence"
    test "$(${pkgs.buildPackages.coreutils}/bin/head -n 1 \
      "$out/bin/kodi-screenshot-evidence")" = \
      ${lib.escapeShellArg "#!${pkgs.python3}/bin/python3"}
    ${pkgs.buildPackages.gnugrep}/bin/grep -Fq \
      ${lib.escapeShellArg "SCREENSHOT_DIRECTORY = \"${screenshotPath}\""} \
      "$out/bin/kodi-screenshot-evidence"
    ${pkgs.buildPackages.gnugrep}/bin/grep -Fq \
      ${lib.escapeShellArg "PROTOCOL_VERSION = \"KODI-SCREENSHOT-EVIDENCE/1\""} \
      "$out/bin/kodi-screenshot-evidence"
    if ${pkgs.buildPackages.gnugrep}/bin/grep -Fq \
      '@KODI_SCREENSHOT_PATH@' \
      "$out/bin/kodi-screenshot-evidence"
    then
      echo "Screenshot path placeholder was not substituted" >&2
      exit 1
    fi
  '';
}
