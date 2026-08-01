{ pkgs }:

pkgs.writeShellApplication {
  name = "kodi-content-routes";
  runtimeInputs = [ pkgs.python3 ];
  text = ''
    exec python3 ${./reconcile.py} "$@"
  '';
  checkPhase = ''
    PYTHONDONTWRITEBYTECODE=1 \
      ${pkgs.buildPackages.python3}/bin/python3 -B \
      -m unittest discover -s ${./.} -p 'test_*.py'
  '';
}
