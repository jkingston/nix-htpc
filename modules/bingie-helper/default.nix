{
  kodiPackages,
  lib,
  pkgs,
}:
let
  addonId = "script.bingie.helper";
  manifestSha256 =
    "79ea0d00b20513105445bf6e16a0424ca816f77cf4cc26822dcd86874d83cdb6";
  simplejson = kodiPackages.simplejson;
in
kodiPackages.buildKodiAddon rec {
  pname = "bingie-helper";
  namespace = addonId;
  version = "1.1.2";

  src = pkgs.fetchFromGitHub {
    owner = "matke-84";
    repo = "script.bingie.helper";
    rev = "4599ecada369d823843bcf36cb55e9cd67db137a";
    hash = "sha256-3FRQLYSUp4lNMBruMUP+sDFIN/pD20iariHHGxni7QQ=";
  };

  propagatedBuildInputs = [ simplejson ];
  nativeCheckInputs = [
    pkgs.buildPackages.coreutils
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
    test "$(
      xmllint \
        --xpath \
        'string(/addon/extension[@point="xbmc.python.script"]/@library)' \
        addon.xml
    )" = default.py
    test "$(
      xmllint \
        --xpath \
        'string(/addon/requires/import[@addon="script.module.simplejson"]/@version)' \
        addon.xml
    )" = 3.17.0

    for required_file in \
      default.py \
      resources/settings.xml \
      resources/lib/helper.py \
      resources/lib/utils.py
    do
      test -f "$required_file"
    done

    for python_file in \
      default.py \
      resources/__init__.py \
      resources/lib/__init__.py \
      resources/lib/helper.py \
      resources/lib/utils.py
    do
      python3 -c \
        'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_bytes(), filename=sys.argv[1])' \
        "$python_file"
    done

    runHook postCheck
  '';

  postInstall = ''
    addon_dir="$out/share/kodi/addons/${addonId}"
    test "$(sha256sum "$addon_dir/addon.xml" | cut -d ' ' -f 1)" = \
      ${lib.escapeShellArg manifestSha256}
    for installed_file in \
      addon.xml \
      default.py \
      resources/settings.xml \
      resources/lib/helper.py \
      resources/lib/utils.py
    do
      test -f "$addon_dir/$installed_file"
    done
  '';

  passthru = {
    manifestIdentity = {
      inherit version;
      manifestSha256 = manifestSha256;
    };
    provenance = {
      owner = "matke-84";
      repo = "script.bingie.helper";
      rev = "4599ecada369d823843bcf36cb55e9cd67db137a";
      sourceHash =
        "sha256-3FRQLYSUp4lNMBruMUP+sDFIN/pD20iariHHGxni7QQ=";
    };
  };

  meta = {
    description = "Helper add-on for the BINGIE Kodi skin";
    homepage = "https://github.com/matke-84/script.bingie.helper";
    license = lib.licenses.gpl3Only;
  };
}
