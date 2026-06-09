#!/usr/bin/env bash
# Download ApacheJIT split CSV files from Zenodo.
# Usage: ./scripts/download_apachejit.sh [target_dir]
#
# Zenodo publishes a single zip archive (not standalone CSVs). This script
# downloads apachejit_dataset_replication.zip and extracts the three split
# files into a flat target directory.
#
# P0 limitation: no checksum validation on zip or CSVs; a corrupt partial
# download may require manually deleting the zip before re-running.
#
# The dataset is open access (CC BY 4.0) from:
# https://zenodo.org/records/5907847
set -euo pipefail

TARGET_DIR="${1:-data/apachejit}"
mkdir -p "$TARGET_DIR"

ZIP_URL="https://zenodo.org/api/records/5907847/files/apachejit_dataset_replication.zip/content"
ZIP_PATH="$TARGET_DIR/apachejit_dataset_replication.zip"

CSV_FILES=(
    apachejit_train.csv
    apachejit_test_large.csv
    apachejit_test_small.csv
)

if ! command -v unzip >/dev/null 2>&1; then
    echo "Error: unzip is required but not found in PATH." >&2
    exit 1
fi

all_csvs_present() {
    local csv
    for csv in "${CSV_FILES[@]}"; do
        if [ ! -f "$TARGET_DIR/$csv" ]; then
            return 1
        fi
    done
    return 0
}

download() {
    local url="$1" dest="$2"
    if [ -f "$dest" ]; then
        echo "✓ $dest already exists, skipping."
        return
    fi
    echo "↓ Downloading $(basename "$dest")..."
    curl -fSL --progress-bar -o "$dest" "$url"
    echo "✓ Saved $dest"
}

extract_csv() {
    local csv="$1"
    local dest="$TARGET_DIR/$csv"
    if [ -f "$dest" ]; then
        echo "✓ $dest already exists, skipping."
        return
    fi
    if [ ! -f "$ZIP_PATH" ]; then
        echo "Error: $ZIP_PATH not found; cannot extract $csv." >&2
        exit 1
    fi
    echo "↓ Extracting $csv from $(basename "$ZIP_PATH")..."
    unzip -j -o "$ZIP_PATH" "apachejit/dataset/$csv" -d "$TARGET_DIR"
    echo "✓ Saved $dest"
}

if all_csvs_present; then
    echo "✓ All ApacheJIT CSV files already present in $TARGET_DIR"
else
    download "$ZIP_URL" "$ZIP_PATH"
    for csv in "${CSV_FILES[@]}"; do
        extract_csv "$csv"
    done
fi

echo ""
echo "Done. Files in $TARGET_DIR:"
ls -lh "$TARGET_DIR"/*.csv
