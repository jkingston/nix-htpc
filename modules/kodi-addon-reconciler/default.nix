{
  addonSpecs,
  lib,
  pkgs,
}:
let
  activeRoot = "/home/htpc/.kodi/addons";
  backupRoot = "/var/lib/nix-htpc/kodi-addon-backups";
  source = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./main.py
      ./reconciler.py
      ./test_reconciler.py
    ];
  };
  renderIdentity =
    identity:
    assert lib.assertMsg
      (builtins.match "[A-Za-z0-9][A-Za-z0-9.+_~-]{0,63}"
        identity.version != null)
      "Kodi reconciler versions must use the supported syntax";
    assert lib.assertMsg
      (builtins.match "[0-9a-f]{64}" identity.manifestSha256 != null)
      "Kodi reconciler manifest hashes must be lowercase SHA-256";
    {
      version = identity.version;
      manifest_sha256 = identity.manifestSha256;
    };
  renderManaged =
    addonId: managed:
    if managed == null then
      null
    else
      {
        addon_path = "${managed.addon}/share/kodi/addons/${addonId}";
        manifest_path =
          "${managed.addon}/share/kodi/addons/${addonId}/addon.xml";
        identity = renderIdentity managed.identity;
      };
  renderedSpecs = map (
    spec:
    assert lib.assertMsg
      (builtins.match
        "(plugin|resource|script|service)\\.[A-Za-z0-9][A-Za-z0-9_.-]*"
        spec.addonId != null)
      "Kodi reconciler add-on IDs must use a supported namespace";
    {
      addon_id = spec.addonId;
      userdata = renderIdentity spec.userdata;
      managed = renderManaged spec.addonId (spec.managed or null);
    }
  ) addonSpecs;
  managedAddons = map (spec: spec.managed.addon) (
    builtins.filter (spec: (spec.managed or null) != null) addonSpecs
  );
  addonIds = map (spec: spec.addonId) addonSpecs;
  configuration = {
    schema_version = 1;
    active_root = activeRoot;
    backup_root = backupRoot;
    backup_uid = 0;
    backup_gid = 0;
    backup_mode = 448;
    specs = renderedSpecs;
  };
  configurationFile = pkgs.writeText "kodi-addon-reconciler.json" (
    builtins.toJSON configuration
  );
in
assert lib.assertMsg
  (builtins.length addonIds == builtins.length (lib.unique addonIds))
  "Kodi reconciler add-on specifications must have unique IDs";
pkgs.stdenvNoCC.mkDerivation {
  pname = "kodi-addon-reconciler";
  version = "1.0.0";
  src = source;

  nativeBuildInputs = [ pkgs.buildPackages.makeWrapper ];
  nativeCheckInputs = [ pkgs.buildPackages.python3 ];
  doCheck = true;

  postPatch = ''
    substituteInPlace main.py \
      --replace-fail \
        ${lib.escapeShellArg "@CONFIGURATION_PATH@"} \
        ${lib.escapeShellArg "${configurationFile}"}
  '';

  checkPhase = ''
    runHook preCheck
    export PYTHONDONTWRITEBYTECODE=1
    python3 -B -m unittest -v test_reconciler.py
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm0555 main.py "$out/libexec/kodi-addon-reconciler/main.py"
    install -Dm0444 \
      reconciler.py \
      "$out/libexec/kodi-addon-reconciler/reconciler.py"
    makeWrapper \
      ${pkgs.python3}/bin/python3 \
      "$out/bin/kodi-addon-reconciler" \
      --unset PYTHONHOME \
      --unset PYTHONPATH \
      --add-flags \
        "-B $out/libexec/kodi-addon-reconciler/main.py"
    if grep -R -Fq '@CONFIGURATION_PATH@' "$out"; then
      echo "Kodi add-on reconciler configuration was not substituted" >&2
      exit 1
    fi
    grep -Fq 'unset PYTHONHOME' "$out/bin/kodi-addon-reconciler"
    grep -Fq 'unset PYTHONPATH' "$out/bin/kodi-addon-reconciler"
    runHook postInstall
  '';

  passthru = {
    inherit
      activeRoot
      backupRoot
      configuration
      configurationFile
      managedAddons
      ;
  };

  meta = {
    description = "Fail-closed reconciliation of Kodi userdata add-ons";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
  };
}
