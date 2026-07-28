$ErrorActionPreference = "Stop"

# One-command launcher for the Streamlit UI - no manual venv activation needed.
#   .\run_ui.ps1
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "Virtual env not found at .venv. Run .\setup.ps1 first."
    exit 1
}

# Install the UI packages into the venv on first run (find_spec avoids importing
# them). Both are checked by import name rather than by package name: the
# searchbox arrived after the first release of this script, so an existing venv
# has Streamlit and not it.
foreach ($mod in @("streamlit", "streamlit_searchbox")) {
    & $venvPython -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('$mod') else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$mod not installed - installing the UI requirements into the venv..."
        & $venvPython -m pip install streamlit "streamlit-searchbox>=0.1.24,<0.2"
        break
    }
}

Write-Host "Starting the UI at http://localhost:8501  (press Ctrl+C to stop)"
& $venvPython -m streamlit run (Join-Path $root "ui\app.py")
