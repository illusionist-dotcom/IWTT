"""PyInstaller entry point; also runnable from a source checkout."""
from pathlib import Path
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iwtt.desktop import main

main()
