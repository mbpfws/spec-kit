#!/usr/bin/env bash
# Common functions and variables for all scripts

# Get repository root, with fallback for non-git repositories
get_repo_root() {
    if git rev-parse --show-toplevel >/dev/null 2>&1; then
        git rev-parse --show-toplevel
    else
        # Fall back to script location for non-git repos
        local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        (cd "$script_dir/../../.." && pwd)
    fi
}

# Get current branch, with fallback for non-git repositories
get_current_branch() {
    # First check if SPECIFY_FEATURE environment variable is set
    if [[ -n "${SPECIFY_FEATURE:-}" ]]; then
        echo "$SPECIFY_FEATURE"
        return
    fi
    
    # Then check git if available
    if git rev-parse --abbrev-ref HEAD >/dev/null 2>&1; then
        git rev-parse --abbrev-ref HEAD
        return
    fi
    
    # For non-git repos, try to find the latest feature directory
    local repo_root=$(get_repo_root)
    local specs_dir="$repo_root/specs"
    
    if [[ -d "$specs_dir" ]]; then
        local latest_feature=""
        local highest=0
        
        for dir in "$specs_dir"/*; do
            if [[ -d "$dir" ]]; then
                local dirname=$(basename "$dir")
                if [[ "$dirname" =~ ^([0-9]{3})- ]]; then
                    local number=${BASH_REMATCH[1]}
                    number=$((10#$number))
                    if [[ "$number" -gt "$highest" ]]; then
                        highest=$number
                        latest_feature=$dirname
                    fi
                fi
            fi
        done
        
        if [[ -n "$latest_feature" ]]; then
            echo "$latest_feature"
            return
        fi
    fi
    
    echo "main"  # Final fallback
}

# Check if we have git available
has_git() {
    git rev-parse --show-toplevel >/dev/null 2>&1
}

check_feature_branch() {
    local branch="$1"
    local has_git_repo="$2"
    
    # For non-git repos, we can't enforce branch naming but still provide output
    if [[ "$has_git_repo" != "true" ]]; then
        echo "[specify] Warning: Git repository not detected; skipped branch validation" >&2
        return 0
    fi
    
    if [[ ! "$branch" =~ ^[0-9]{3}- ]]; then
        echo "ERROR: Not on a feature branch. Current branch: $branch" >&2
        echo "Feature branches should be named like: 001-feature-name" >&2
        return 1
    fi
    
    return 0
}

get_feature_dir() { echo "$1/specs/$2"; }

get_classification_file() {
    local repo_root="${1:-$(get_repo_root)}"
    echo "$repo_root/.specify/state/project-classification.json"
}

_project_type_from_json() {
    local classification_file="$1"
    local python_cmd="python3"
    if ! command -v python3 >/dev/null 2>&1; then
        if command -v python >/dev/null 2>&1; then
            python_cmd="python"
        else
            echo "greenfield"
            return
        fi
    fi

    "$python_cmd" - <<'PY'
import json, os, sys
path = os.environ.get("CLASSIFICATION_PATH")
try:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    project_type = data.get("project_type") or "greenfield"
except (FileNotFoundError, json.JSONDecodeError, OSError):
    project_type = "greenfield"
print(project_type)
PY
}

get_project_type() {
    local repo_root="${1:-$(get_repo_root)}"
    local classification_file
    classification_file=$(get_classification_file "$repo_root")
    if [[ -f "$classification_file" ]]; then
        CLASSIFICATION_PATH="$classification_file" _project_type_from_json "$classification_file"
    else
        echo "greenfield"
    fi
}

get_feature_paths() {
    local repo_root=$(get_repo_root)
    local current_branch=$(get_current_branch)
    local has_git_repo="false"
    
    if has_git; then
        has_git_repo="true"
    fi
    
    local feature_dir=$(get_feature_dir "$repo_root" "$current_branch")
    local project_type=$(get_project_type "$repo_root")
    
    cat <<EOF
REPO_ROOT='$repo_root'
CURRENT_BRANCH='$current_branch'
HAS_GIT='$has_git_repo'
FEATURE_DIR='$feature_dir'
FEATURE_SPEC='$feature_dir/spec.md'
IMPL_PLAN='$feature_dir/plan.md'
TASKS='$feature_dir/tasks.md'
RESEARCH='$feature_dir/research.md'
DATA_MODEL='$feature_dir/data-model.md'
QUICKSTART='$feature_dir/quickstart.md'
CONTRACTS_DIR='$feature_dir/contracts'
PROJECT_TYPE='$project_type'
EOF
}

check_file() { [[ -f "$1" ]] && echo "  ✓ $2" || echo "  ✗ $2"; }
check_dir() { [[ -d "$1" && -n $(ls -A "$1" 2>/dev/null) ]] && echo "  ✓ $2" || echo "  ✗ $2"; }

# GAID System Functions
register_gaid() {
    local gaid="$1"
    local path="$2"
    local stage="$3"
    local domain="$4"
    local dependencies="$5"
    local project_type="$6"
    
    local repo_root=$(get_repo_root)
    local registry_file="$repo_root/.specify/state/artifact-registry.json"
    local state_dir="$(dirname "$registry_file")"
    
    mkdir -p "$state_dir"
    
    # Create or update registry entry
    python3 - <<PY
import json
import os
import hashlib
from datetime import datetime

registry_file = "$registry_file"
entries = []

if os.path.exists(registry_file):
    try:
        with open(registry_file, 'r') as f:
            entries = json.load(f)
    except:
        entries = []

# Remove existing entry for same path
entries = [e for e in entries if e.get('path') != '$path']

# Create new entry
entry = {
    "gaid": "$gaid",
    "path": "$path",
    "stage": "$stage",
    "domain": "$domain",
    "dependencies": "$dependencies".split(',') if "$dependencies" else [],
    "project_type": "$project_type",
    "created_at": datetime.now().isoformat(),
    "checksum": ""
}

if os.path.exists("$path"):
    with open("$path", 'rb') as f:
        entry["checksum"] = hashlib.md5(f.read()).hexdigest()

entries.append(entry)

with open(registry_file, 'w') as f:
    json.dump(entries, f, indent=2)

print("$gaid")
PY
}

get_next_gaid() {
    local domain="$1"
    local repo_root=$(get_repo_root)
    local registry_file="$repo_root/.specify/state/artifact-registry.json"
    
    python3 - <<PY
import json
import os

registry_file = "$registry_file"
entries = []

if os.path.exists(registry_file):
    try:
        with open(registry_file, 'r') as f:
            entries = json.load(f)
    except:
        entries = []

# Find highest number for domain
highest = 0
for entry in entries:
    gaid = entry.get('gaid', '')
    if gaid.startswith('GAID-' + "$domain".upper() + '-'):
        try:
            num = int(gaid.split('-')[-1])
            if num > highest:
                highest = num
        except:
            pass

print(f"GAID-{"$domain".upper()}-{highest + 1:03d}")
PY
}

get_gaid_context() {
    local repo_root=$(get_repo_root)
    local registry_file="$repo_root/.specify/state/artifact-registry.json"
    
    if [[ -f "$registry_file" ]]; then
        python3 - <<PY
import json
import os

registry_file = "$registry_file"
entries = []

try:
    with open(registry_file, 'r') as f:
        entries = json.load(f)
except:
    entries = []

# Get current branch context
current_branch = os.environ.get('CURRENT_BRANCH', '')
if not current_branch:
    current_branch = "$(get_current_branch)"

# Find GAIDs for current branch
feature_gaids = []
for entry in entries:
    if current_branch in entry.get('path', ''):
        feature_gaids.append({
            'gaid': entry.get('gaid', ''),
            'domain': entry.get('domain', ''),
            'stage': entry.get('stage', ''),
            'dependencies': entry.get('dependencies', [])
        })

result = {
    'current_branch': current_branch,
    'artifacts': feature_gaids
}

print(json.dumps(result))
PY
    else
        echo '{}'
    fi
}
