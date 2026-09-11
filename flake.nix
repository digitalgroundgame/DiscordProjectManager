{
  description = "Discord-Native Task Management Platform (dgg-pm)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forEachSupportedSystem = f: nixpkgs.lib.genAttrs supportedSystems (system: f {
        pkgs = import nixpkgs { inherit system; };
      });
    in
    {
      devShells = forEachSupportedSystem ({ pkgs }: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            python313
            uv
            postgresql_16
            gnumake
            docker
            git
            curl
            ruff
          ];

          env = {
            # Ensure C extensions and native wheels find system dynamic libraries on NixOS
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath (with pkgs; [
              stdenv.cc.cc.lib
              zlib
              postgresql_16
              openssl
            ]);
            PYTHONUNBUFFERED = "1";
          };

          shellHook = ''
            if [ -f .env ]; then
              set -a
              source .env
              set +a
            fi
            echo "🚀 dgg-pm Nix dev shell ready (Python $(python3 --version 2>/dev/null | cut -d' ' -f2), uv $(uv --version 2>/dev/null | cut -d' ' -f2))"
          '';
        };
      });
    };
}
