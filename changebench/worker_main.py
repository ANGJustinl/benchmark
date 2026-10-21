"""Executable worker shim works both from a checkout and an installed wheel."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from changebench.live_agent import main

if __name__ == "__main__":
    raise SystemExit(main())
