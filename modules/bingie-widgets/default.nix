{
  kodiPackages,
  lib,
  pkgs,
}:
let
  addonId = "script.bingie.widgets";
  manifestSha256 =
    "3957ad5d2296cfd8f058bbd1746e051a287eee68eef9afbabd15ee77588edbcd";
in
kodiPackages.buildKodiAddon rec {
  pname = "bingie-widgets";
  namespace = addonId;
  version = "1.0.3";

  src = ./src;

  nativeCheckInputs = [
    pkgs.buildPackages.coreutils
    pkgs.buildPackages.findutils
    pkgs.buildPackages.libxml2
    pkgs.buildPackages.python3
  ];
  doCheck = true;

  checkPhase = ''
    runHook preCheck

    test "$(sha256sum addon.xml | cut -d ' ' -f 1)" = \
      ${lib.escapeShellArg manifestSha256}
    xmllint --nonet --noout addon.xml
    test "$(xmllint --xpath 'string(/addon/@id)' addon.xml)" = \
      ${lib.escapeShellArg addonId}
    test "$(xmllint --xpath 'string(/addon/@version)' addon.xml)" = \
      ${lib.escapeShellArg version}
    test "$(xmllint --xpath \
      'string(/addon/extension[@point="xbmc.python.pluginsource"]/@library)' \
      addon.xml)" = plugin.py
    test "$(xmllint --xpath \
      'string(/addon/extension[@point="xbmc.service"]/@library)' \
      addon.xml)" = service.py

    for required_file in \
      plugin.py \
      service.py \
      resources/settings.xml \
      resources/lib/main.py \
      resources/lib/media.py
    do
      test -f "$required_file"
    done

    find . -name '*.py' -type f -print0 \
      | xargs -0 -n1 python3 -c \
        'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_bytes(), filename=sys.argv[1])'
    PYTHONDONTWRITEBYTECODE=1 \
      python3 -B -m unittest discover -s . -p 'test_*.py'

    runHook postCheck
  '';

  postInstall = ''
    addon_dir="$out/share/kodi/addons/${addonId}"
    test "$(sha256sum "$addon_dir/addon.xml" | cut -d ' ' -f 1)" = \
      ${lib.escapeShellArg manifestSha256}
    test -f "$addon_dir/plugin.py"
    test -f "$addon_dir/service.py"
    test -f "$addon_dir/resources/lib/main.py"
  '';

  passthru.manifestIdentity = {
    inherit version;
    manifestSha256 = manifestSha256;
  };

  meta = {
    description = "Local-library widgets for the BINGIE Kodi skin";
    homepage = "https://github.com/matke-84/repository.bingie";
    license = lib.licenses.gpl2Only;
  };
}
