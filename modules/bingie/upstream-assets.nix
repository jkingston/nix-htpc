{ pkgs }:

let
  version = "2.0.2";
  url = "https://raw.githubusercontent.com/matke-84/repository.bingie/main/omega/skin.bingie/skin.bingie-2.0.2.zip";
  hash = "sha256-kK9EzmO/yEAp2LNh0Wf4hkPHHaX37F1JsJ3xU9Tn12g=";
  source = pkgs.fetchzip {
    inherit url hash;
  };
in
{
  inherit
    hash
    source
    url
    version
    ;

  # Every directory below contains only immutable binary payloads in the
  # pinned 2.0.2 archive. Textual skin source is owned under ./src instead.
  binaryPaths = [
    "fonts"
    "media"
    "resources"
    "extras/media"
    "extras/viewthumbs"
    "extras/skinthemes/Reset.jpg"
  ];
}
