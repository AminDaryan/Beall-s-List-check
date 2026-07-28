#!/usr/bin/env bash
# One-command launcher for the Streamlit UI - no manual venv activation needed.
#   ./run_ui.sh
set -e
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

py="$root/.venv/bin/python"
if [ ! -x "$py" ]; then
    echo "Virtual env not found at .venv/. Run ./setup.sh first." >&2
    exit 1
fi

# An existing .venv is not necessarily a usable one. Where python3 lacks
# ensurepip -- the stock Debian and Ubuntu case -- `python3 -m venv` leaves the
# directory behind with this interpreter in it and no pip, so the check above
# passes and the install below fails with "No module named pip". Say what is
# actually wrong, and leave the removing to the reader: this script has no
# business deleting a directory it did not create.
if ! "$py" -m pip --version >/dev/null 2>&1; then
    echo "The virtual env at .venv/ has no pip, so it was not created completely." >&2
    echo "Remove it and run setup again:" >&2
    echo >&2
    echo "    rm -rf .venv && ./setup.sh" >&2
    exit 1
fi

if ! "$py" -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('streamlit') else 1)"; then
    echo "Streamlit not installed - installing it into the venv..."
    "$py" -m pip install streamlit
fi

echo "Starting the UI at http://localhost:8501  (press Ctrl+C to stop)"
exec "$py" -m streamlit run "$root/ui/app.py"
