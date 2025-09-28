#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage: register-artifact.sh --gaid <GAID> --path <artifact_path> \
    --stage </init|/constitution|/specify|/clarify|/plan|/tasks|/analyze|/implement|/validate> \
    --domain <domain_identifier> [--project-type <type>] \
    [--dependencies dep1,dep2] [--agents agent1,agent2] [--metadata KEY=VALUE] [--dry-run]

Registers or updates a GAID entry in .specify/state/artifact-registry.json.

OPTIONS:
  --gaid             Required GAID identifier (e.g., GAID-PLN-0001)
  --path             Relative path to governing artifact (e.g., specs/001-feature/plan.md)
  --stage            Lifecycle stage (init,/constitution,/specify,/clarify,/plan,/tasks,/analyze,/implement,/validate)
  --domain           Architectural/governance domain slug (e.g., planning, research, architecture)
  --project-type     Project classification (greenfield, brownfield, ongoing)
  --dependencies     Comma-separated GAID references this artifact depends on
  --agents           Comma-separated agent identifiers synchronized with this artifact
  --metadata         Additional KEY=VALUE pair (can repeat). Stored under metadata object
  --dry-run          Output resulting JSON to stdout without writing to disk
  --help             Show this help message

Examples:
  register-artifact.sh --gaid GAID-PLN-0001 --path specs/001-sample/plan.md \
      --stage /plan --domain architecture --project-type greenfield

EOF
}

error() {
    echo "ERROR: $1" >&2
    exit 1
}

info() {
    echo "[register-artifact] $1"
}

require_command() {
    local cmd="$1"
    command -v "$cmd" >/dev/null 2>&1 || error "Required command '$cmd' not found"
}

require_command jq
require_command python3

GAID=""
ARTIFACT_PATH=""
STAGE=""
DOMAIN=""
PROJECT_TYPE=""
DEPENDENCIES=""
AGENTS=""
DRY_RUN=false
declare -A METADATA

while [[ $# -gt 0 ]]; do
    case "$1" in
        --gaid)
            GAID="$2"; shift 2 ;;
        --path)
            ARTIFACT_PATH="$2"; shift 2 ;;
        --stage)
            STAGE="$2"; shift 2 ;;
        --domain)
            DOMAIN="$2"; shift 2 ;;
        --project-type)
            PROJECT_TYPE="$2"; shift 2 ;;
        --dependencies)
            DEPENDENCIES="$2"; shift 2 ;;
        --agents)
            AGENTS="$2"; shift 2 ;;
        --metadata)
            if [[ "$2" != *=* ]]; then
                error "--metadata expects KEY=VALUE"
            fi
            key="${2%%=*}"
            value="${2#*=}"
            METADATA[$key]="$value"
            shift 2 ;;
        --dry-run)
            DRY_RUN=true; shift ;;
        --help|-h)
            usage; exit 0 ;;
        *)
            error "Unknown option: $1" ;;
    esac
done

[[ -z "$GAID" ]] && error "--gaid is required"
[[ -z "$ARTIFACT_PATH" ]] && error "--path is required"
[[ -z "$STAGE" ]] && error "--stage is required"
[[ -z "$DOMAIN" ]] && error "--domain is required"

normalize_stage() {
    case "$1" in
        /init|init) echo "/init" ;;
        /constitution|constitution) echo "/constitution" ;;
        /specify|specify) echo "/specify" ;;
        /clarify|clarify) echo "/clarify" ;;
        /plan|plan) echo "/plan" ;;
        /tasks|tasks) echo "/tasks" ;;
        /analyze|analyze) echo "/analyze" ;;
        /implement|implement) echo "/implement" ;;
        /validate|validate) echo "/validate" ;;
        *) return 1 ;;
    esac
}

if ! STAGE=$(normalize_stage "$STAGE"); then
    error "Invalid stage '$STAGE'"
fi

REPO_ROOT="$(get_repo_root)"
STATE_DIR="$REPO_ROOT/.specify/state"
REGISTRY_FILE="$STATE_DIR/artifact-registry.json"

mkdir -p "$STATE_DIR"
ABS_PATH="$REPO_ROOT/$ARTIFACT_PATH"
if [[ ! -e "$ABS_PATH" ]]; then
    error "Artifact path does not exist: $ARTIFACT_PATH (resolved: $ABS_PATH)"
fi

NORMALIZED_PATH=$(python3 - <<PY
import os, sys
path, repo = sys.argv[1:]
abs_path = os.path.abspath(os.path.join(repo, path))
relative = os.path.relpath(abs_path, repo)
print(relative.replace("\\", "/"))
PY
"$ARTIFACT_PATH" "$REPO_ROOT")

timestamp() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

parse_csv_lower() {
    local input="$1"
    local -n out_ref=$2
    out_ref=()
    IFS=',' read -r -a raw <<<"$input"
    for item in "${raw[@]}"; do
        item="${item//[$'\t\r\n ']/}"
        [[ -n "$item" ]] && out_ref+=("${item,,}")
    done
}

parse_csv_lower "$DEPENDENCIES" DEP_ARRAY
parse_csv_lower "$AGENTS" AGENT_ARRAY

PROJECT_TYPE=${PROJECT_TYPE,,}
if [[ -n "$PROJECT_TYPE" && ! "$PROJECT_TYPE" =~ ^(greenfield|brownfield|ongoing)$ ]]; then
    error "Invalid project type '$PROJECT_TYPE'"
fi

METADATA_JSON="{}"
for key in "${!METADATA[@]}"; do
    value="${METADATA[$key]}"
    METADATA_JSON=$(jq --arg k "$key" --arg v "$value" '. + {($k): $v}' <<<"$METADATA_JSON")
done

if [[ ! -f "$REGISTRY_FILE" ]]; then
    echo "[]" >"$REGISTRY_FILE"
fi

TMP_FILE=$(mktemp)
trap 'rm -f "$TMP_FILE"' EXIT

if ! jq empty "$REGISTRY_FILE" >/dev/null 2>&1; then
    error "Existing registry file contains invalid JSON: $REGISTRY_FILE"
fi

REGISTRY_CONTENT=$(cat "$REGISTRY_FILE")

DEPENDENCY_JSON=$(python3 - <<'PY'
import json, sys
items = [arg for arg in sys.argv[1:] if arg]
print(json.dumps(items))
PY
"${DEP_ARRAY[@]}")

AGENT_JSON=$(python3 - <<'PY'
import json, sys
items = [arg for arg in sys.argv[1:] if arg]
print(json.dumps(items))
PY
"${AGENT_ARRAY[@]}")

CHECKSUM=$(python3 - <<'PY'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], 'rb') as fh:
    for chunk in iter(lambda: fh.read(8192), b''):
        h.update(chunk)
print(h.hexdigest())
PY
"$ABS_PATH")

NEW_ENTRY=$(jq -n \
    --arg gaid "$GAID" \
    --arg path "$NORMALIZED_PATH" \
    --arg stage "$STAGE" \
    --arg domain "$DOMAIN" \
    --arg project_type "$PROJECT_TYPE" \
    --argjson dependencies "$DEPENDENCY_JSON" \
    --argjson agents "$AGENT_JSON" \
    --argjson metadata "$METADATA_JSON" \
    --arg last_synced "$(timestamp)" \
    --arg checksum "$CHECKSUM" \
    '{
        gaid: $gaid,
        path: $path,
        stage: $stage,
        domain: $domain,
        project_type: ($project_type | select(. != "")) ,
        dependencies: $dependencies,
        agents: $agents,
        metadata: $metadata,
        status: "registered",
        last_synced_at: $last_synced,
        checksum: $checksum
    }')

UPDATED=$(jq \
    --arg gaid "$GAID" \
    --argjson entry "$NEW_ENTRY" \
    'map(select(.gaid != $gaid)) + [$entry]' <<<"$REGISTRY_CONTENT")

if $DRY_RUN; then
    printf '%s
' "$UPDATED"
    info "Dry run complete; registry not updated"
else
    printf '%s' "$UPDATED" >"$TMP_FILE"
    mv "$TMP_FILE" "$REGISTRY_FILE"
    info "Registered $GAID at $NORMALIZED_PATH"
fi
