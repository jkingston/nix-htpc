{ pkgs, ... }:

let
  pier = pkgs.python312Packages.buildPythonApplication {
    pname = "pier";
    version = "0.1.0";
    pyproject = true;

    src = ../pier;

    build-system = [ pkgs.python312Packages.hatchling ];

    dependencies = with pkgs.python312Packages; [
      click
      rich
      httpx
      vdf
    ];

    # Skip tests during build
    doCheck = false;
  };
in
{
  # Pier - ROM management CLI for NixOS HTPC
  environment.systemPackages = [
    pier
    # steam-run is required for running AppImages and non-Nix binaries
    pkgs.steam-run
  ];

  # Enable AppImage support via binfmt_misc (extracts and runs automatically)
  # Required for Harbour Masters ports (SoH, 2Ship, SpaghettiKart, etc.)
  programs.appimage = {
    enable = true;
    binfmt = true;
  };
}
