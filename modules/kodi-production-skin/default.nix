{ pkgs }:

pkgs.stdenvNoCC.mkDerivation {
  pname = "kodi-production-skin";
  version = "1";
  src = ./.;

  nativeCheckInputs = [ pkgs.buildPackages.python3 ];
  doCheck = true;
  checkPhase = ''
    runHook preCheck
    PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v test_reconcile.py
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm0555 reconcile.py "$out/bin/kodi-production-skin"
    patchShebangs "$out/bin/kodi-production-skin"
    runHook postInstall
  '';
}
