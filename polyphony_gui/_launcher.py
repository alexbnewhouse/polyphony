"""
polyphony_gui._launcher
=======================
Minimal entry point for the ``polyphony-gui`` console script.

Kept deliberately free of top-level Streamlit imports so that loading this
module (just to access ``launch``) does not trigger "missing ScriptRunContext"
warnings — those fire whenever ``streamlit`` is imported outside of a running
script context.
"""

from __future__ import annotations

from pathlib import Path


def launch() -> None:
    """Start the Polyphony Streamlit app."""
    import sys

    from streamlit.web.cli import main

    app_path = str(Path(__file__).parent / "app.py")
    sys.argv = ["streamlit", "run", app_path, "--server.headless", "true"]
    main()
