"""Build the local application assets included in the Python wheel."""

import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
npm = shutil.which("npm.cmd") or shutil.which("npm")
if npm is None:
    raise SystemExit("Node.js/npm are required to build frontend assets")
subprocess.run([npm, "run", "build"], cwd=root / "frontend", check=True)
