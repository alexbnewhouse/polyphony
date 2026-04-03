#!/bin/bash
# Launch the Polyphony GUI
# Usage: ./run.sh [--port 8501]
#
# Preferred: pip install polyphony[gui] && polyphony-gui

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure polyphony[gui] is installed
if ! python3 -c "import polyphony_gui" 2>/dev/null; then
    echo "Installing polyphony with GUI extras..."
    pip3 install -e "$REPO_ROOT[gui]" -q
fi

echo ""
echo "Starting Polyphony GUI..."
echo "Open your browser at: http://localhost:8501"
echo ""

python3 -m polyphony_gui "$@"
