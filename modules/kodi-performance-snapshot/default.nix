{ pkgs }:

pkgs.stdenvNoCC.mkDerivation {
  pname = "kodi-performance-snapshot";
  version = "1";
  src = ./.;
  strictDeps = true;
  nativeCheckInputs = [ pkgs.buildPackages.python3 ];
  dontBuild = true;
  doCheck = true;

  checkPhase = ''
    runHook preCheck
    PYTHONDONTWRITEBYTECODE=1 \
      python3 -I -B -m unittest discover -s . -p 'test_*.py'
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -D -m 0555 snapshot.py "$out/bin/kodi-performance-snapshot"
    substituteInPlace "$out/bin/kodi-performance-snapshot" \
      --replace-fail '#!/usr/bin/env python3' '#!${pkgs.python3}/bin/python3 -I'
    runHook postInstall
  '';
}
