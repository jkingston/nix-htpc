{ lib, pkgs, addonVersion }:

let
  pythonShebang = "#!${pkgs.python3}/bin/python3 -I";
in
pkgs.stdenvNoCC.mkDerivation {
  pname = "kodi-settings-watchdog";
  version = "1";
  src = ./.;
  strictDeps = true;

  nativeCheckInputs = [
    pkgs.buildPackages.python3
  ];

  dontBuild = true;
  doCheck = true;

  postPatch = ''
    substituteInPlace watchdog.py \
      --replace-fail \
        ${lib.escapeShellArg "#!/usr/bin/env python3"} \
        ${lib.escapeShellArg pythonShebang} \
      --replace-fail \
        ${lib.escapeShellArg "@KODI_SETTINGS_ADDON_VERSION@"} \
        ${lib.escapeShellArg addonVersion}
  '';

  checkPhase = ''
    runHook preCheck
    PYTHONDONTWRITEBYTECODE=1 \
      python3 -I -B -m unittest discover -s . -p 'test_*.py'
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -D -m 0555 watchdog.py "$out/bin/kodi-settings-watchdog"
    runHook postInstall
  '';

  postInstall = ''
    program="$out/bin/kodi-settings-watchdog"
    test -x "$program"
    test "$(${pkgs.buildPackages.coreutils}/bin/stat -c %a "$program")" = 555
    test "$(${pkgs.buildPackages.coreutils}/bin/head -n 1 "$program")" = \
      ${lib.escapeShellArg pythonShebang}
    ${pkgs.buildPackages.gnugrep}/bin/grep -Fq \
      ${lib.escapeShellArg "EXPECTED_ADDON_VERSION = \"${addonVersion}\""} \
      "$program"
    for method in \
      XBMC.GetInfoLabels \
      Addons.GetAddonDetails \
      Addons.SetAddonEnabled
    do
      ${pkgs.buildPackages.gnugrep}/bin/grep -Fq "$method" "$program"
    done
    if ${pkgs.buildPackages.gnugrep}/bin/grep -Eq \
      '@KODI_SETTINGS_ADDON_VERSION@|^#!/usr/bin/env ' \
      "$program"
    then
      echo "Kodi settings watchdog contains an unresolved placeholder" >&2
      exit 1
    fi
    if ${pkgs.buildPackages.gnugrep}/bin/grep -Eq \
      'Player\\.|GUI\\.|System\\.|CEC' \
      "$program"
    then
      echo "Kodi settings watchdog contains a forbidden control method" >&2
      exit 1
    fi
  '';
}
