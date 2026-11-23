#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 ]] || { echo "Ubuntu 24.04 required"; exit 1; }
[[ $EUID != 0 ]] || { echo "Run as the Studio owner, with sudo access."; exit 1; }
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv ffmpeg git ca-certificates curl xz-utils
umask 077
export PATH="$PWD/.private/node/bin:$PATH"
if ! command -v npm >/dev/null || ! node -e "if(Number(process.versions.node.split('.')[0])<20)process.exit(1)" 2>/dev/null; then
  case "$(uname -m)" in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; *) echo "Install Node 22 for this CPU"; exit 1 ;; esac
  mkdir -p .private
  cache=$(mktemp -d "$PWD/.private/node-install.XXXXXX")
  url=https://nodejs.org/dist/latest-v22.x
  curl --fail --silent --show-error --location "$url/SHASUMS256.txt" -o "$cache/checksums"
  file=$(awk -v arch="$arch" '$2 ~ ("^node-v22[.][0-9]+[.][0-9]+-linux-" arch "[.]tar[.]xz$") {print $2}' "$cache/checksums")
  [[ "$file" =~ ^node-v22\.[0-9]+\.[0-9]+-linux-(x64|arm64)\.tar\.xz$ ]] || { echo "Node release manifest unavailable"; exit 1; }
  curl --fail --silent --show-error --location "$url/$file" -o "$cache/$file"
  (cd "$cache"; awk -v name="$file" '$2==name' checksums | sha256sum -c -)
  tar -xJf "$cache/$file" -C "$cache"
  mkdir -p .private/node
  cp -a "$cache/${file%.tar.xz}/." .private/node/
  rm -r -- "$cache"
fi
[[ -e .env ]] || cp .env.example .env
[[ -x .venv/bin/python ]] || python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix frontend ci --no-audit --no-fund
echo "Studio dependencies ready. Configure .env and run bash scripts/deploy_ubuntu.sh."
