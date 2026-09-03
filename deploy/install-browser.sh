#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_VENV="${ROOT_DIR}/.venv/bin/python"
SHARED_VENV="${ROOT_DIR}/../venv/bin/python"
PYTHON_BIN="${GENERAL_SEARCH_PYTHON:-${PROJECT_VENV}}"

if [[ ! -x "${PYTHON_BIN}" && -x "${SHARED_VENV}" ]]; then
  PYTHON_BIN="${SHARED_VENV}"
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python environment not found at ${PROJECT_VENV} or ${SHARED_VENV}" >&2
  exit 1
fi

export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${ROOT_DIR}/../playwright-browsers}"
mkdir -p "${PLAYWRIGHT_BROWSERS_PATH}"
"${PYTHON_BIN}" -m playwright install chromium

echo "Playwright Chromium installed at ${PLAYWRIGHT_BROWSERS_PATH}."
