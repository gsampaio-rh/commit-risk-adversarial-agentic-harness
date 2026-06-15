#!/usr/bin/env bash
# Clone Apache project repositories for commit investigation.
# Usage: ./scripts/clone_apache_repos.sh [target_dir]
#
# Idempotent: skips repos that already exist.
# Default target: data/repos/
#
# ApacheJIT project→repo mapping (not all are 1:1):
#   HDFS, MAPREDUCE → apache/hadoop (single monorepo)
#   AMQ             → apache/activemq
#   All others      → apache/{project_lowercase}

set -euo pipefail

TARGET_DIR="${1:-data/repos}"

REPOS=(
    "https://github.com/apache/camel.git"
    "https://github.com/apache/hadoop.git"
    "https://github.com/apache/hbase.git"
    "https://github.com/apache/spark.git"
    "https://github.com/apache/groovy.git"
    "https://github.com/apache/ignite.git"
    "https://github.com/apache/hive.git"
    "https://github.com/apache/flink.git"
    "https://github.com/apache/cassandra.git"
    "https://github.com/apache/activemq.git"
)

# ApacheJIT uses HDFS/MAPREDUCE as project names but both live in the
# hadoop monorepo.  Symlinks let GitContextProvider.for_project() find
# data/repos/hdfs/ and data/repos/mapreduce/ without a code change.
SYMLINKS=(
    "hdfs:hadoop"
    "mapreduce:hadoop"
    "amq:activemq"
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

for pair in "${SYMLINKS[@]}"; do
    alias_name="${pair%%:*}"
    target_name="${pair##*:}"
    link_path="$TARGET_DIR/$alias_name"

    if [ -L "$link_path" ]; then
        echo "✓ symlink $alias_name → $target_name already exists (skipping)"
    elif [ -d "$link_path" ]; then
        echo "⚠ $link_path exists as a directory, not a symlink — skipping"
    else
        ln -s "$target_name" "$link_path"
        echo "✓ symlink $alias_name → $target_name created"
    fi
done

echo ""
echo "All repos ready under $TARGET_DIR/"
du -sh "$TARGET_DIR"/* 2>/dev/null || true
