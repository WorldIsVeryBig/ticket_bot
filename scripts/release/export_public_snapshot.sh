#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FILE_LIST="${ROOT_DIR}/scripts/release/public-files.txt"
VERSION="${1:-2.0.0}"
OUT_DIR="${2:-${ROOT_DIR}/dist/ticket-bot-public-v${VERSION}}"

if [ -e "${OUT_DIR}" ] && [ -n "$(ls -A "${OUT_DIR}" 2>/dev/null)" ]; then
  echo "Target directory already exists and is not empty: ${OUT_DIR}" >&2
  echo "Choose a different output directory or remove it first." >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

while IFS= read -r path; do
  if [[ -z "${path}" || "${path}" == \#* ]]; then
    continue
  fi

  src_path="${ROOT_DIR}/${path}"
  dst_path="${OUT_DIR}/${path}"

  if [ ! -e "${src_path}" ]; then
    echo "Skipping missing path: ${path}" >&2
    continue
  fi

  mkdir -p "$(dirname "${dst_path}")"
  if [ -d "${src_path}" ]; then
    rsync -a \
      --exclude '__pycache__/' \
      --exclude '.pytest_cache/' \
      --exclude '*.pyc' \
      --exclude '*.pyo' \
      --exclude '.DS_Store' \
      --exclude '*.log' \
      --exclude 'data/' \
      --exclude 'model/' \
      --exclude 'ticket-filter/' \
      --exclude 'scratch/' \
      "${src_path}/" "${dst_path}/"
  else
    cp "${src_path}" "${dst_path}"
  fi
done < "${FILE_LIST}"

echo "Public snapshot created at: ${OUT_DIR}"
echo "Next steps:"
echo "  1. Review the exported tree for anything environment-specific."
echo "  2. Copy one of the example configs to config.yaml before running it."
echo "  3. Publish the exported tree to a new public repository or orphan branch."
