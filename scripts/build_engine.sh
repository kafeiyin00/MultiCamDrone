#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Fetch and build Unreal Engine 5.2 as an *installed build*, which is what the
# Project AirSim plugin needs to compile against without the engine recompiling
# itself on every plugin iteration.
#
#   ./scripts/build_engine.sh fetch      # clone + Setup.sh   (~35GB, ~30min)
#   ./scripts/build_engine.sh build      # installed build    (~200GB, 4h+)
#   ./scripts/build_engine.sh all
#   ./scripts/build_engine.sh status
#
# Requires a GitHub account linked to an Epic Games account -- EpicGames/
# UnrealEngine is a private repo. Check with:
#     git ls-remote git@github.com:EpicGames/UnrealEngine.git HEAD
#
# The whole tree lives under engine/ and is git-ignored.
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UE_BRANCH="${UE_BRANCH:-5.2}"
UE_DIR="${REPO_ROOT}/engine/UnrealEngine-${UE_BRANCH}"
INSTALLED_DIR="${UE_DIR}/LocalBuilds/Engine/Linux"

ok()   { echo -e "  \033[32m[ok]\033[0m   $*"; }
info() { echo -e "  \033[36m[info]\033[0m $*"; }
warn() { echo -e "  \033[33m[warn]\033[0m $*"; }
die()  { echo -e "  \033[31m[FAIL]\033[0m $*" >&2; exit 1; }

need_space() {
  local need_gb="$1"
  local avail_gb
  avail_gb=$(df -BG --output=avail "$REPO_ROOT" | tail -1 | tr -dc '0-9')
  if (( avail_gb < need_gb )); then
    die "need ~${need_gb}GB free, only ${avail_gb}GB available on $(df -h --output=target "$REPO_ROOT" | tail -1)"
  fi
  ok "${avail_gb}GB free (need ~${need_gb}GB)"
}

cmd_fetch() {
  echo "=== Fetching Unreal Engine ${UE_BRANCH} ==="
  need_space 60

  if [[ ! -d "${UE_DIR}/.git" ]]; then
    info "Checking access to the Epic Games repo ..."
    git ls-remote git@github.com:EpicGames/UnrealEngine.git HEAD >/dev/null 2>&1 \
      || die "cannot reach EpicGames/UnrealEngine. Link your GitHub account to
         an Epic Games account: https://www.unrealengine.com/en-US/ue-on-github"
    ok "repo reachable"

    mkdir -p "${REPO_ROOT}/engine"
    info "Cloning branch ${UE_BRANCH} (shallow; history is not needed to build) ..."
    git clone --depth 1 --branch "${UE_BRANCH}" \
        git@github.com:EpicGames/UnrealEngine.git "$UE_DIR"
  else
    ok "source already present at ${UE_DIR}"
  fi

  # Setup.sh pulls the binary dependencies the git repo deliberately omits.
  # It is resumable, so re-running after an interruption is safe.
  info "Running Setup.sh (downloads ~30GB of engine dependencies) ..."
  (cd "$UE_DIR" && ./Setup.sh)
  ok "engine dependencies in place ($(du -sh "$UE_DIR" | cut -f1))"
}

cmd_build() {
  echo "=== Building an installed build of UE ${UE_BRANCH} ==="
  [[ -d "$UE_DIR" ]] || die "no engine source at ${UE_DIR}; run '$0 fetch' first"
  need_space 220

  cd "$UE_DIR"

  # Upstream's instructions: this platform directory trips up the installed
  # build graph if present.
  rm -rf Engine/Platforms/XXX

  info "Starting BuildGraph. This takes 4+ hours and saturates the CPU."
  info "Shipping is included because packaging Blocks needs it."
  ./Engine/Build/BatchFiles/RunUAT.sh BuildGraph \
      -target="Make Installed Build Linux" \
      -script=Engine/Build/InstalledEngineBuild.xml \
      -set:HostPlatformOnly=true \
      -set:WithLinuxAArch64=false \
      -set:WithFullDebugInfo=false \
      -set:WithDDC=true \
      -set:GameConfigurations="DebugGame;Development;Shipping"

  [[ -d "$INSTALLED_DIR" ]] \
    || die "BuildGraph finished but ${INSTALLED_DIR} is missing"
  ok "installed build at ${INSTALLED_DIR} ($(du -sh "$INSTALLED_DIR" | cut -f1))"
  echo
  echo "Point Project AirSim at it when building the plugin:"
  echo "    export UE_ROOT=\"${INSTALLED_DIR}\""
}

cmd_status() {
  echo "=== Engine status ==="
  if [[ -d "${UE_DIR}/.git" ]]; then
    ok "source   ${UE_DIR} ($(du -sh "$UE_DIR" 2>/dev/null | cut -f1))"
  else
    warn "source   not fetched"
  fi
  if [[ -d "${UE_DIR}/Engine/Binaries/ThirdParty" ]]; then
    ok "deps     Setup.sh has run"
  else
    warn "deps     Setup.sh not completed"
  fi
  if [[ -d "$INSTALLED_DIR" ]]; then
    ok "build    ${INSTALLED_DIR} ($(du -sh "$INSTALLED_DIR" 2>/dev/null | cut -f1))"
  else
    warn "build    installed build not made"
  fi
  df -h "$REPO_ROOT" | tail -1 | awk '{print "  disk     " $4 " free of " $2}'
}

case "${1:-status}" in
  fetch)  cmd_fetch ;;
  build)  cmd_build ;;
  all)    cmd_fetch; cmd_build ;;
  status) cmd_status ;;
  -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command '$1' (fetch | build | all | status)" ;;
esac
