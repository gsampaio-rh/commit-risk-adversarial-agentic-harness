#!/usr/bin/env bash
# Clone Apache Camel and Hadoop repositories for commit investigation.
# Usage: ./scripts/clone_apache_repos.sh [target_dir]
#
# Idempotent: skips repos that already exist.
# Default target: data/repos/

set -euo pipefail

TARGET_DIR="${1:-data/repos}"

REPOS=(
    "https://github.com/apache/camel.git"
    "https://github.com/apache/hadoop.git"
)

mkdir -p "$TARGET_DIR"

for repo_url in "${REPOS[@]}"; do
    repo_name=$(basename "$repo_url" .git)
    repo_path="$TARGET_DIR/$repo_name"

    if [ -d "$repo_path/.git" ]; then
        echo "✓ $repo_name already cloned at $repo_path (skipping)"
    else
        echo "→ Cloning $repo_name into $repo_path..."
        git clone --progress "$repo_url" "$repo_path"
        echo "✓ $repo_name cloned successfully"
    fi
done

echo ""
echo "All repos ready under $TARGET_DIR/"
du -sh "$TARGET_DIR"/* 2>/dev/null || true
