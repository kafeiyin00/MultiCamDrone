#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Apply this repo's patches to the ProjectAirSim submodule.
#
#   ./scripts/apply_patches.sh            # apply anything not yet applied
#   ./scripts/apply_patches.sh --check    # report status, change nothing
#   ./scripts/apply_patches.sh --revert   # take the submodule back to upstream
#
# ProjectAirSim is vendored as a submodule pinned to an upstream commit, so
# local changes to it cannot be committed here. They live in patches/ instead
# and are re-applied after a fresh clone or a submodule update.
#
# After applying, the simulator has to be rebuilt for the changes to take
# effect -- see docs/FISHEYE.md section 7.
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUB="${REPO_ROOT}/ProjectAirSim"
PATCH_DIR="${REPO_ROOT}/patches"

ok()   { echo -e "  \033[32m[ok]\033[0m   $*"; }
info() { echo -e "  \033[36m[info]\033[0m $*"; }
warn() { echo -e "  \033[33m[warn]\033[0m $*"; }
die()  { echo -e "  \033[31m[FAIL]\033[0m $*" >&2; exit 1; }

[[ -d "${SUB}/.git" || -f "${SUB}/.git" ]] \
  || die "submodule not checked out. Run: git submodule update --init --recursive"

shopt -s nullglob
PATCHES=( "${PATCH_DIR}"/*.patch )
shopt -u nullglob
[[ ${#PATCHES[@]} -gt 0 ]] || die "no patches found in ${PATCH_DIR}"

MODE="apply"
case "${1:-}" in
  --check)  MODE="check" ;;
  --revert) MODE="revert" ;;
  -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) die "unknown option '$1' (--check | --revert)" ;;
esac

if [[ "$MODE" == "revert" ]]; then
  echo "Reverting ${SUB} to its pinned upstream commit"
  # Patches add files as well as edit them, so a plain checkout is not enough.
  git -C "$SUB" checkout -- .
  git -C "$SUB" clean -fd \
      unreal/Blocks/Plugins/ProjectAirSim/Source/ProjectAirSim/Private/Sensors \
      unreal/Blocks/Plugins/ProjectAirSim/Source/ProjectAirSim/Private/Shaders
  ok "submodule back at $(git -C "$SUB" log --oneline -1)"
  exit 0
fi

echo "=============================================================="
echo " ProjectAirSim patches"
echo "=============================================================="
echo "  submodule at $(git -C "$SUB" log --oneline -1)"
echo

FAILED=0
for patch in "${PATCHES[@]}"; do
  name="$(basename "$patch")"

  if git -C "$SUB" apply --reverse --check "$patch" >/dev/null 2>&1; then
    ok "${name} — already applied"
    continue
  fi

  if ! git -C "$SUB" apply --check "$patch" >/dev/null 2>&1; then
    warn "${name} — does not apply cleanly"
    info "the submodule may have moved, or the tree is partially patched;"
    info "'--revert' then re-run, or refresh the patch against the new upstream"
    FAILED=1
    continue
  fi

  if [[ "$MODE" == "check" ]]; then
    info "${name} — applies cleanly, not yet applied"
    continue
  fi

  git -C "$SUB" apply "$patch"
  ok "${name} — applied"
done

echo
if [[ "$MODE" == "check" ]]; then
  echo "Check only; nothing was changed."
elif [[ $FAILED -eq 0 ]]; then
  cat <<EOF
Patches are in place. Rebuild for them to take effect:

    export UE_ROOT=${REPO_ROOT}/engine/UnrealEngine-5.2
    export PATH="/usr/bin:\$UE_ROOT/Engine/Binaries/ThirdParty/DotNet/6.0.302/linux:\$PATH"
    cd ProjectAirSim
    ./build.sh simlibs_release
    ./build.sh package_blocks_shipping

Note the /usr/bin prefix: a pip-installed cmake 4.x in ~/.local/bin drops
support for cmake_minimum_required(VERSION < 3.5), which JSBSim still uses, and
the build fails at the JSBSim configure step.
EOF
fi
exit $FAILED
