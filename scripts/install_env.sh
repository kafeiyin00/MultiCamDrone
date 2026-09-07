#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Install a locally-packaged Unreal environment into envs/ so the sim container
# can launch it.
#
#   ./scripts/install_env.sh                        # Blocks Shipping -> envs/BlocksFisheye
#   ./scripts/install_env.sh --name Blocks          # overwrite the stock Blocks
#   ./scripts/install_env.sh --src <dir> --name X
#
# `./build.sh package_blocks_shipping` leaves its output under
# ProjectAirSim/packages/Blocks/Shipping in the same shape as the release zips
# (Linux/<Name>.sh next to Linux/<Name>/). The sim entrypoint expects
# envs/<Name>/<Name>.sh, so this flattens that one level, exactly as
# fetch_env.sh does for a downloaded archive.
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO_ROOT}/ProjectAirSim/packages/Blocks/Shipping"
NAME="BlocksFisheye"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src)  SRC="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[ERROR] unknown option '$1'" >&2; exit 1 ;;
  esac
done

ok()   { echo -e "  \033[32m[ok]\033[0m   $*"; }
info() { echo -e "  \033[36m[info]\033[0m $*"; }
die()  { echo -e "  \033[31m[FAIL]\033[0m $*" >&2; exit 1; }

[[ -d "$SRC" ]] || die "no packaged build at ${SRC}
        Build one first:
          export UE_ROOT=${REPO_ROOT}/engine/UnrealEngine-5.2
          export PATH=\"/usr/bin:\$UE_ROOT/Engine/Binaries/ThirdParty/DotNet/6.0.302/linux:\$PATH\"
          cd ProjectAirSim && ./build.sh package_blocks_shipping"

# The launcher is what identifies a usable package; find it rather than assuming
# a fixed depth, since UAT's archive layout has moved between engine versions.
LAUNCHER="$(find "$SRC" -maxdepth 3 -name '*.sh' -type f \
            -not -path '*/Engine/*' -not -name 'get_ps_servers.sh' | head -n1)"
[[ -n "$LAUNCHER" ]] || die "no launcher .sh found under ${SRC}"

PKG_ROOT="$(dirname "$LAUNCHER")"
SRC_NAME="$(basename "$LAUNCHER" .sh)"
info "packaged '${SRC_NAME}' at ${PKG_ROOT}"

DEST="${REPO_ROOT}/envs/${NAME}"
if [[ -e "$DEST" ]]; then
  info "replacing existing ${DEST}"
  # Move aside rather than delete first, so a failed copy does not leave the
  # environment missing entirely.
  BACKUP="${DEST}.replacing.$$"
  mv "$DEST" "$BACKUP"
  trap 'if [[ -d "${BACKUP:-}" ]]; then rm -rf "$DEST"; mv "$BACKUP" "$DEST"; echo "[restored previous ${NAME}]" >&2; fi' ERR
fi

mkdir -p "$DEST"
info "copying ($(du -sh "$PKG_ROOT" | cut -f1)) ..."
cp -a "${PKG_ROOT}/." "$DEST/"

# The sim entrypoint looks for <Name>.sh, so rename when installing under a
# different name than the project was built as.
if [[ "$SRC_NAME" != "$NAME" ]]; then
  mv "${DEST}/${SRC_NAME}.sh" "${DEST}/${NAME}.sh"
  info "launcher renamed ${SRC_NAME}.sh -> ${NAME}.sh (payload dir stays ${SRC_NAME}/)"
fi

chmod +x "${DEST}/${NAME}.sh" 2>/dev/null || true
find "${DEST}" -path "*/Binaries/Linux/*" -type f -exec chmod +x {} \; 2>/dev/null || true

trap - ERR
[[ -n "${BACKUP:-}" && -d "${BACKUP:-}" ]] && rm -rf "$BACKUP"

ok "${NAME} installed at ${DEST} ($(du -sh "$DEST" | cut -f1))"
echo
echo "Launch it with:"
echo "    SIM_ENV_NAME=${NAME} ./scripts/run_sim.sh"
