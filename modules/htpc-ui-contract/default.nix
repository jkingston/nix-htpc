{ lib, stdenvNoCC, python3 }:

stdenvNoCC.mkDerivation {
  pname = "htpc-ui-contract";
  version = "1";
  src = ./.;

  nativeCheckInputs = [ python3 ];
  doCheck = true;
  dontBuild = true;

  checkPhase = ''
    runHook preCheck
    python3 -m unittest -v test_contract.py
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -D -m 0444 README.md "$out/share/htpc-ui-contract/README.md"
    install -D -m 0444 contract.py "$out/share/htpc-ui-contract/contract.py"
    install -D -m 0444 contracts/home.json "$out/share/htpc-ui-contract/contracts/home.json"
    install -D -m 0444 contracts/playback.json "$out/share/htpc-ui-contract/contracts/playback.json"
    runHook postInstall
  '';

  meta = {
    description = "Versioned UI protocol shared by the HTPC skin and services";
    license = lib.licenses.mit;
    platforms = lib.platforms.all;
  };
}
