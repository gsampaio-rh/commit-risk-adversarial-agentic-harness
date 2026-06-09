#!/usr/bin/env bash
# Download BPI Challenge 2014 CSV files from 4TU.ResearchData.
# Usage: ./scripts/download_bpi2014.sh [target_dir]
#
# The dataset is open access (CC BY 4.0) from:
# https://data.4tu.nl/articles/dataset/BPI_Challenge_2014/12690831
set -euo pipefail

TARGET_DIR="${1:-data/bpi2014}"
mkdir -p "$TARGET_DIR"

CHANGE_URL="https://data.4tu.nl/file/44daf6e4-5730-445c-b823-ca498484ea32/b665b726-c065-4668-8e97-96a225a39598"
INCIDENT_URL="https://data.4tu.nl/file/44daf6e4-5730-445c-b823-ca498484ea32/76b3da71-6aed-438f-adf4-5048c14d5ade"

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

download "$CHANGE_URL" "$TARGET_DIR/Detail_Change.csv"
download "$INCIDENT_URL" "$TARGET_DIR/Detail_Incident.csv"

echo ""
echo "Done. Files in $TARGET_DIR:"
ls -lh "$TARGET_DIR"/*.csv
