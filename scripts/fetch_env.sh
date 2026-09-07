#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Download and unpack a pre-built Project AirSim environment into envs/.
#
# The packaged Unreal environments are hundreds of MB to several GB, so they are
# git-ignored rather than committed. This script fetches them back.
#
#   ./scripts/fetch_env.sh                 # Blocks (default, ~550MB)
#   ./scripts/fetch_env.sh Neighborhood    # ~1.8GB
#   ./scripts/fetch_env.sh CityEnviron     # ~3.2GB, split archive
#   ./scripts/fetch_env.sh --list
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOWNLOADS="${REPO_ROOT}/downloads"
ENVS="${REPO_ROOT}/envs"
RELEASE="${AIRSIM_RELEASE:-v0.3.0}"
BASE_URL="https://github.com/iamaisim/ProjectAirSim/releases/download/${RELEASE}"

# Environments published as a single Linux zip for this release.
SINGLE_ENVS=(Blocks LandscapeMountains Neighborhood)
# Environments split across .zip.001 / .zip.002 parts.
SPLIT_ENVS=(CityEnviron)

usage() {
  cat <<EOF
Usage: $(basename "$0") [ENV_NAME | --list]

Environments available in release ${RELEASE}:
  Blocks              ~0.55 GB   minimal test environment (default)
  LandscapeMountains  ~0.58 GB   outdoor terrain
  Neighborhood        ~1.8  GB   suburban streets
  CityEnviron         ~3.2  GB   dense city (split archive)

Override the release with AIRSIM_RELEASE, e.g.
  AIRSIM_RELEASE=v0.2.0 $(basename "$0") Blocks
EOF
}

ENV_NAME="${1:-Blocks}"
case "$ENV_NAME" in
  -h|--help|--list) usage; exit 0 ;;
esac

is_in() { local n="$1"; shift; for e in "$@"; do [[ "$e" == "$n" ]] && return 0; done; return 1; }

die_bad_checksum() {
  echo "[ERROR] The nested archive failed its sha256 check. The download is" >&2
  echo "        corrupt; delete it from downloads/ and re-run." >&2
  exit 1
}

if ! is_in "$ENV_NAME" "${SINGLE_ENVS[@]}" && ! is_in "$ENV_NAME" "${SPLIT_ENVS[@]}"; then
  echo "[ERROR] Unknown environment: $ENV_NAME" >&2; echo >&2; usage >&2; exit 1
fi

mkdir -p "$DOWNLOADS" "$ENVS"

if [[ -d "${ENVS}/${ENV_NAME}" ]]; then
  echo "[skip] ${ENVS}/${ENV_NAME} already exists. Delete it to re-download."
  exit 0
fi

version="${RELEASE#v}"
zip_path="${DOWNLOADS}/${ENV_NAME}-Linux-${version}.zip"

if is_in "$ENV_NAME" "${SPLIT_ENVS[@]}"; then
  echo "[info] ${ENV_NAME} ships as a split archive; fetching both parts."
  for part in 001 002; do
    out="${zip_path}.${part}"
    echo "[info] Downloading $(basename "$out") ..."
    curl -L --fail --retry 3 -C - -o "$out" "${BASE_URL}/${ENV_NAME}-Linux-${version}.zip.${part}"
  done
  echo "[info] Joining parts ..."
  cat "${zip_path}."00[12] > "$zip_path"
else
  echo "[info] Downloading $(basename "$zip_path") ..."
  curl -L --fail --retry 3 -C - -o "$zip_path" "${BASE_URL}/${ENV_NAME}-Linux-${version}.zip"
fi

# The archives unpack to Linux/<Name>.sh + Linux/<Name>/. Flatten that one level
# so every environment ends up as envs/<Name>/<Name>.sh, which is the layout the
# sim container's entrypoint expects.
#
# Some releases (CityEnviron in v0.3.0) ship a zip containing *another* zip plus
# a manifest and a .sha256, so unpacking has to recurse and verify.
tmp="${ENVS}/.unpack_${ENV_NAME}_$$"
echo "[info] Extracting into ${ENVS}/${ENV_NAME} ..."
rm -rf "$tmp"; mkdir -p "$tmp"
unzip -q "$zip_path" -d "$tmp"

# Nested archive: verify it against its checksum, then unpack it in place.
inner="$(find "$tmp" -maxdepth 2 -name '*.zip' -type f | head -n1)"
if [[ -n "$inner" ]]; then
  echo "[info] Found a nested archive: $(basename "$inner")"
  if [[ -f "${inner}.sha256" ]]; then
    echo "[info] Verifying sha256 ..."
    ( cd "$(dirname "$inner")" && sha256sum -c "$(basename "$inner").sha256" ) \
      || die_bad_checksum
  else
    echo "[warn] No .sha256 alongside the nested archive; skipping verification."
  fi
  inner_dir="${tmp}/.inner"
  mkdir -p "$inner_dir"
  unzip -q "$inner" -d "$inner_dir"
  # Keep the manifests, drop the 3GB archive we have already unpacked.
  find "$tmp" -maxdepth 2 -name '*.manifest.json' -exec mv {} "$inner_dir/" \; 2>/dev/null || true
  rm -f "$inner" "${inner}.sha256"
  tmp="$inner_dir"
fi

if [[ -d "${tmp}/Linux" ]]; then
  mv "${tmp}/Linux" "${ENVS}/${ENV_NAME}"
  # Keep the build manifest alongside the payload.
  find "$tmp" -maxdepth 1 -type f -exec mv {} "${ENVS}/${ENV_NAME}/" \; 2>/dev/null || true
else
  mv "$tmp" "${ENVS}/${ENV_NAME}"
fi
rm -rf "${ENVS}/.unpack_${ENV_NAME}_$$"

chmod +x "${ENVS}/${ENV_NAME}/${ENV_NAME}.sh" 2>/dev/null || true
chmod +x "${ENVS}/${ENV_NAME}/${ENV_NAME}/Binaries/Linux/"* 2>/dev/null || true

echo "[ok]   ${ENV_NAME} ready at ${ENVS}/${ENV_NAME} ($(du -sh "${ENVS}/${ENV_NAME}" | cut -f1))"
echo "[hint] Launch it with:  SIM_ENV_NAME=${ENV_NAME} docker compose -f docker/docker-compose.yml up sim"
