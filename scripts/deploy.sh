#!/usr/bin/env bash
# Install or upgrade OpenLLM-Eval with Docker Compose. Run it from the unpacked release folder:
#
#   bash scripts/deploy.sh                 # install or upgrade, then run the smoke test
#   bash scripts/deploy.sh --mock          # also start the mock model server
#   bash scripts/deploy.sh --tools         # also start the sandboxed tool runner (integration mode)
#
# Options:
#   --mock                     start the mock-llm service (same as --profile mock)
#   --tools                    start the tool-runner service (same as --profile tools); needs
#                              TOOL_EXECUTION_ENABLED=true in .env to be used by runs
#   --profile NAME             enable a Compose profile (repeatable)
#   --take-data-from PROJECT   replace this install's database with the one from an older
#                              Compose project (this install's data is backed up first)
#   --keep-new-data            never offer to replace this install's database
#   --no-smoke                 skip scripts/smoke-test.sh at the end
#
# Every release uses the Compose project name "openllm-eval", so a new release unpacked into any
# folder upgrades the same stack in place. Older releases ran under a project named after their
# folder. This script finds those (any container built from an openllm-eval image), stops and
# removes their containers and networks so the ports are free, and carries their database over.
# Volumes are never deleted.
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT=openllm-eval
COMPOSE=(docker compose -p "$PROJECT")

profiles=()
take_from=""
keep_new=0
smoke=1

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while (($#)); do
  case "$1" in
    --mock) profiles+=(--profile mock) ;;
    --tools) profiles+=(--profile tools) ;;
    --profile) profiles+=(--profile "${2:?--profile needs a name}"); shift ;;
    --take-data-from) take_from="${2:?--take-data-from needs a project name}"; shift ;;
    --keep-new-data) keep_new=1 ;;
    --no-smoke) smoke=0 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mWARN\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31mERROR\033[0m %s\n' "$*" >&2; exit 1; }
confirm() { # Interactive yes/no. Without a terminal the answer is always no.
  [[ -t 0 ]] || return 1
  local reply
  read -r -p "$1 [y/N] " reply
  [[ "$reply" =~ ^[Yy] ]]
}
env_value() { sed -n "s/^$1=//p" .env | tail -n 1 | tr -d "\"'\r"; }
project_containers() { docker ps -aq --filter "label=com.docker.compose.project=$1"; }
pg_volume() {
  docker volume ls -q --filter "label=com.docker.compose.project=$1" \
    --filter "label=com.docker.compose.volume=postgres_data" | head -n 1
}

# --- Preflight -------------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "docker is not installed"
docker info >/dev/null 2>&1 || die "cannot reach the Docker daemon (is it running? are you in the docker group?)"
compose_version="$(docker compose version --short 2>/dev/null | sed 's/^v//')" || true
[[ -n "$compose_version" ]] || die "the Docker Compose v2 plugin ('docker compose') is not installed"
IFS=. read -r c_major c_minor _ <<<"$compose_version"
if ((c_major < 2 || (c_major == 2 && ${c_minor%%[!0-9]*} < 24))); then
  die "Docker Compose $compose_version is too old; 2.24 or later is required"
fi
[[ -f .env ]] || die ".env not found. Copy it from your previous install, or: cp .env.example .env (then set POSTGRES_PASSWORD)"

# --- Authentication secrets (Milestone 8) -----------------------------------------------------
# Generated once and kept in .env, so upgrades reuse them. Letters and digits only, so they need
# no quoting in .env.
random_string() { { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; } || true; }
ensure_env() { # ensure_env NAME LENGTH: append NAME=<random> if NAME is missing or empty
  if [[ -z "$(env_value "$1")" ]]; then
    [[ -z "$(tail -c 1 .env)" ]] || echo >>.env
    if grep -q "^$1=" .env; then
      sed -i.bak "s/^$1=.*/$1=$(random_string "$2")/" .env && rm -f .env.bak
    else
      echo "$1=$(random_string "$2")" >>.env
    fi
    info "Generated $1 in .env"
  fi
}
if [[ "$(env_value AUTH_ENABLED | tr '[:upper:]' '[:lower:]')" != "false" ]]; then
  ensure_env AUTH_SECRET_KEY 48
  ensure_env ADMIN_INITIAL_PASSWORD 20
fi

# --- Tool sandbox (Milestone 9) ----------------------------------------------------------------
tools_enabled="$(env_value TOOL_EXECUTION_ENABLED | tr '[:upper:]' '[:lower:]')"
if [[ " ${profiles[*]-} " == *" tools "* ]]; then
  ensure_env TOOL_RUNNER_TOKEN 48
  if [[ ! -f tools/tools.yaml ]]; then
    cp tools/tools.example.yaml tools/tools.yaml
    info "Created tools/tools.yaml from tools/tools.example.yaml (review it: these tools will run)"
  fi
  [[ "$tools_enabled" == "true" ]] \
    || warn "The tool runner starts, but runs cannot use it until TOOL_EXECUTION_ENABLED=true in .env."
elif [[ "$tools_enabled" == "true" ]]; then
  warn "TOOL_EXECUTION_ENABLED=true but the tool runner is not started; add --tools."
fi

# --- Find installs from older releases that used a different project name -------------------
legacy=()
while read -r proj image; do
  [[ -n "$proj" && "$proj" != "$PROJECT" ]] || continue
  case "$image" in
    openllm-eval-backend | openllm-eval-backend:* | openllm-eval-frontend | openllm-eval-frontend:*) ;;
    *) continue ;;
  esac
  [[ " ${legacy[*]-} " == *" $proj "* ]] || legacy+=("$proj")
done < <(docker ps -a --format '{{.Label "com.docker.compose.project"}} {{.Image}}')

# Keep the mock server running if the install being replaced had it.
if ((${#profiles[@]} == 0)) && docker ps -q --filter "label=com.docker.compose.service=mock-llm" \
  --filter status=running | grep -q .; then
  info "mock-llm is running in the install being replaced; keeping it (profile 'mock')"
  profiles+=(--profile mock)
fi
if [[ " ${profiles[*]-} " != *" tools "* ]] && docker ps -q \
  --filter "label=com.docker.compose.service=tool-runner" --filter status=running | grep -q .; then
  info "tool-runner is running in the install being replaced; keeping it (profile 'tools')"
  profiles+=(--profile tools)
fi

if ((${#legacy[@]})); then
  info "Found older install(s) under another project name: ${legacy[*]}"
fi

# --- Decide whether to carry a database over ---------------------------------------------------
src_project=""
new_vol="$(pg_volume "$PROJECT")"
if [[ -n "$take_from" ]]; then
  [[ "$take_from" != "$PROJECT" ]] || die "--take-data-from must name another project, not $PROJECT"
  [[ -n "$(pg_volume "$take_from")" ]] || die "Compose project '$take_from' has no postgres_data volume"
  src_project="$take_from"
else
  candidates=()
  for p in ${legacy[@]+"${legacy[@]}"}; do
    [[ -z "$(pg_volume "$p")" ]] || candidates+=("$p")
  done
  if ((${#candidates[@]} > 1)); then
    warn "Several older installs have a database: ${candidates[*]}"
    warn "Not copying any. Choose one with: bash scripts/deploy.sh --take-data-from <project>"
  elif ((${#candidates[@]} == 1)); then
    old="${candidates[0]}"
    if [[ -z "$new_vol" ]]; then
      info "Will carry the database over from '$old'"
      src_project="$old"
    elif ((keep_new == 0)); then
      warn "Both '$PROJECT' and the older install '$old' have a database:"
      warn "  $new_vol (created $(docker volume inspect -f '{{.CreatedAt}}' "$new_vol"))"
      warn "  $(pg_volume "$old") (created $(docker volume inspect -f '{{.CreatedAt}}' "$(pg_volume "$old")"))"
      if confirm "Replace the '$PROJECT' database with the one from '$old'? (it is backed up first)"; then
        src_project="$old"
      else
        warn "Keeping the '$PROJECT' database. To switch later: bash scripts/deploy.sh --take-data-from $old"
      fi
    fi
  fi
fi

# --- Stop older installs (containers and networks only; volumes are kept) ---------------------
to_stop=(${legacy[@]+"${legacy[@]}"})
if [[ -n "$src_project" && " ${to_stop[*]-} " != *" $src_project "* ]]; then to_stop+=("$src_project"); fi
for p in ${to_stop[@]+"${to_stop[@]}"}; do
  ids="$(project_containers "$p")"
  if [[ -n "$ids" ]]; then
    info "Stopping and removing containers of '$p' (its volumes are kept)"
    # shellcheck disable=SC2086
    docker stop -t 30 $ids >/dev/null
    # shellcheck disable=SC2086
    docker rm $ids >/dev/null
  fi
  nets="$(docker network ls -q --filter "label=com.docker.compose.project=$p")"
  # shellcheck disable=SC2086
  [[ -z "$nets" ]] || docker network rm $nets >/dev/null || warn "could not remove networks of '$p'"
done

# --- Copy the database volume --------------------------------------------------------------------
if [[ -n "$src_project" ]]; then
  src_vol="$(pg_volume "$src_project")"
  pg_image="$(sed -n 's/^ *image: *\(postgres:[^ ]*\).*/\1/p' docker-compose.yml | head -n 1)"
  [[ -n "$pg_image" ]] || die "could not find the postgres image in docker-compose.yml"
  copy_volume() {
    docker run --rm --user 0 --entrypoint sh -v "$1":/from:ro -v "$2":/to "$pg_image" \
      -c 'find /to -mindepth 1 -delete && cp -a /from/. /to/'
  }
  # Nothing may use the target database while it is replaced.
  "${COMPOSE[@]}" --profile '*' down --remove-orphans >/dev/null 2>&1 \
    || "${COMPOSE[@]}" down --remove-orphans
  if [[ -n "$new_vol" ]]; then
    backup="${new_vol}_backup_$(date +%Y%m%d%H%M%S)"
    info "Backing up $new_vol to $backup"
    docker volume create "$backup" >/dev/null
    copy_volume "$new_vol" "$backup"
  else
    new_vol="${PROJECT}_postgres_data"
    docker volume create \
      --label "com.docker.compose.project=$PROJECT" \
      --label "com.docker.compose.volume=postgres_data" \
      --label "com.docker.compose.version=$compose_version" \
      "$new_vol" >/dev/null
  fi
  info "Copying database $src_vol -> $new_vol"
  copy_volume "$src_vol" "$new_vol"
fi

# --- Make sure the ports are free -------------------------------------------------------------
api_port="$(env_value API_PORT)"
frontend_port="$(env_value FRONTEND_PORT)"
for port in "${api_port:-8000}" "${frontend_port:-3000}"; do
  owners="$(docker ps --format '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Ports}}' \
    | grep -E ":$port->" | grep -v "|$PROJECT|" | cut -d'|' -f1 || true)"
  if [[ -n "$owners" ]]; then
    die "port $port is used by other container(s): $owners. Stop them, or change the port in .env"
  fi
  if command -v ss >/dev/null 2>&1 \
    && ss -Hltn "sport = :$port" 2>/dev/null | grep -q . \
    && ! docker ps --format '{{.Ports}}' | grep -qE ":$port->"; then
    die "port $port is used by a process outside Docker. Stop it, or change the port in .env"
  fi
done

# --- Build and start ---------------------------------------------------------------------------
info "Building and starting '$PROJECT' (migrations run automatically)"
"${COMPOSE[@]}" ${profiles[@]+"${profiles[@]}"} up -d --build --remove-orphans

if ((smoke)); then
  COMPOSE="${COMPOSE[*]}" bash scripts/smoke-test.sh
fi

# --- First sign-in ------------------------------------------------------------------------------
if [[ "$(env_value AUTH_ENABLED | tr '[:upper:]' '[:lower:]')" == "false" ]]; then
  warn "AUTH_ENABLED=false: anyone who can reach the UI is an administrator."
else
  admin_name="$(env_value ADMIN_USERNAME)"
  admin_name="${admin_name:-admin}"
  # The bootstrap administrator still has its initial password until someone signs in and
  # changes it; only then is the password printed.
  if "${COMPOSE[@]}" exec -T api python -m app.cli.admin list-users 2>/dev/null \
    | grep -E "^$admin_name +admin .*must-change-password" >/dev/null; then
    info "Sign in at http://<this host>:${frontend_port:-3000} as '$admin_name' with password: $(env_value ADMIN_INITIAL_PASSWORD)"
    info "You must choose a new password at the first sign-in. (The password is ADMIN_INITIAL_PASSWORD in .env.)"
  fi
fi

# --- Leftovers -----------------------------------------------------------------------------------
leftovers=()
for p in ${to_stop[@]+"${to_stop[@]}"}; do
  while read -r v; do [[ -z "$v" ]] || leftovers+=("$v"); done \
    < <(docker volume ls -q --filter "label=com.docker.compose.project=$p")
done
if ((${#leftovers[@]})); then
  info "Volumes of older installs were kept. Once you've checked this install, remove them with:"
  echo "    docker volume rm ${leftovers[*]}"
fi
info "Done. Old images can be reclaimed with: docker image prune"
