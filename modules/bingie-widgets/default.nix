{
  kodiPackages,
  lib,
  pkgs,
}:
let
  addonId = "script.bingie.widgets";
  manifestSha256 =
    "8c9bd5fe40b1027da3888677c5320cde351cbaee517ee95e1073a3c5dd4e8123";
in
kodiPackages.buildKodiAddon rec {
  pname = "bingie-widgets";
  namespace = addonId;
  version = "1.0.1";

  src = pkgs.fetchzip {
    url = "https://raw.githubusercontent.com/matke-84/repository.bingie/main/omega/script.bingie.widgets/script.bingie.widgets-${version}.zip";
    hash = "sha256-Q7cnpEmxzrOgoXQJ2H+xpT1gWJ4Lr2txpPytqF9YAu8=";
  };

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
