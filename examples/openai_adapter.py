"""Trusted worker entrypoint; stdin=request JSON, stdout=response JSON."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from changebench.live_agent import main
raise SystemExit(main())
