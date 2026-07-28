#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Debian and Ubuntu ship python3 without ensurepip, so on a stock system
# `python3 -m venv` fails -- and fails *after* creating the directory, leaving a
# .venv that holds an interpreter and no pip. Nothing downstream can tell that
# apart from a working one: run_ui.sh finds an executable .venv/bin/python, goes
# on to install Streamlit into it, and reports "No module named pip", which
# sends the reader after entirely the wrong problem.
#
# Checked here instead, before anything is created, and named. The apt error
# asks for a version-specific package (python3.12-venv); the unversioned name
# pulls in whichever one matches the python3 in use.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    echo "This python3 cannot create virtual environments: the ensurepip module" >&2
    echo "is missing, which on Debian and Ubuntu means the venv package is not" >&2
    echo "installed. Install it and run this script again:" >&2
    echo >&2
    echo "    sudo apt install python3-venv" >&2
    echo >&2
    echo "python3 is: $(command -v python3) ($(python3 --version 2>&1))" >&2
    exit 1
fi

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

echo "Setup complete."
echo "Activate environment with: source .venv/bin/activate"
