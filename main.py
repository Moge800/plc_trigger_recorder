"""Root entry-point launcher — adds src/ to sys.path and runs src/main.py."""

import runpy
import sys
from pathlib import Path

_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if __name__ == "__main__":
    runpy.run_path(str(_SRC / "main.py"), run_name="__main__")
