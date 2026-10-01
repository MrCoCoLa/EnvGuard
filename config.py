import sys
from pathlib import Path

# Traverse upwards, prioritizing explicit repository root markers
curr = Path(__file__).resolve().parent
found_root = None

while curr != curr.parent:
    if (curr / ".git").is_dir() or (curr / "pyproject.toml").is_file():
        found_root = curr
        break
    if any((curr / marker).exists() for marker in ("setup.py", "setup.cfg", "scripts")):
        found_root = curr
        break
    curr = curr.parent

ROOT_DIR = found_root or Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))