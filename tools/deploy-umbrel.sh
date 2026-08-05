#!/bin/sh

set -eu

branch=${DEPLOY_BRANCH:-master}
project_dir=$(git rev-parse --show-toplevel)
cd "$project_dir"

if [ ! -f .env ]; then
    echo "Refusing to deploy without $project_dir/.env" >&2
    exit 1
fi

env_mode=$(stat -c '%a' .env)
if [ "$env_mode" != "600" ] && [ "$env_mode" != "400" ]; then
    echo "Refusing to deploy: .env permissions are $env_mode; expected 600 or 400" >&2
    exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Refusing to deploy with modified tracked files" >&2
    git status --short >&2
    exit 1
fi

current_branch=$(git branch --show-current)
if [ "$current_branch" != "$branch" ]; then
    echo "Refusing to deploy branch $current_branch; expected $branch" >&2
    exit 1
fi

echo "Fetching origin/$branch..."
git fetch origin "$branch"
git merge --ff-only "origin/$branch"

echo "Running tests..."
python3 -m unittest discover -s tests -v

deployed_commit=$(git rev-parse HEAD)
echo "Building and starting $deployed_commit..."
sudo env BLOCKCLOCK_ADAPTER_VERSION="$deployed_commit" docker compose up -d --build

attempt=0
healthy=0
while [ "$attempt" -lt 30 ]; do
    if EXPECTED_COMMIT="$deployed_commit" python3 - 2>/dev/null <<'PY'
import json
import os
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:21022/status", timeout=3) as response:
    status = json.load(response)

assert status["deployed_commit"] == os.environ["EXPECTED_COMMIT"]
assert status["display_error"] is None
assert status["errors"] == {}
assert status["refreshed_at"] is not None
PY
    then
        healthy=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 2
done

if [ "$healthy" -ne 1 ]; then
    echo "Deployment did not become healthy at $deployed_commit" >&2
    sudo docker compose logs --tail=100 blockclock-adapter >&2
    exit 1
fi

sudo docker compose ps
curl -fsS http://127.0.0.1:21022/status
printf '\nDeployed %s successfully.\n' "$deployed_commit"
