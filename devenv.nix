{ pkgs, lib, config, inputs, ... }:

{
  # Discord-Native Task Management Platform (dgg-pm)
  # https://devenv.sh/reference/options/

  languages.python = {
    enable = true;
    package = pkgs.python313;
    libraries = with pkgs; [
      stdenv.cc.cc.lib
      zlib
      postgresql_18
      openssl
    ];
  };

  # Development tools and runtime utilities
  packages = with pkgs; [
    uv
    postgresql_18
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
      postgresql_18
      openssl
    ]);
    PYTHONUNBUFFERED = "1";
  };

  enterShell = ''
    if [ -f .env ]; then
      set -a
      source .env
      set +a
    fi
    echo "🚀 dgg-pm devenv ready (Python $(python3 --version 2>/dev/null | cut -d' ' -f2), uv $(uv --version 2>/dev/null | cut -d' ' -f2))"
  '';
}
