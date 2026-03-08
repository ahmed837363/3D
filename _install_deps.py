"""Install Warp XPBD dependencies."""
import subprocess
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(BASE_DIR, "_install_log.txt")

# Find Python
venv_python = os.path.join(BASE_DIR, "venv", "Scripts", "python.exe")
if os.path.exists(venv_python):
    python = venv_python
else:
    python = sys.executable

packages = ["warp-lang", "trimesh", "numpy", "scipy", "matplotlib", "shapely"]

with open(log_path, "w") as f:
    f.write(f"Python: {python}\n")
    f.write(f"Installing: {', '.join(packages)}\n\n")

    for pkg in packages:
        f.write(f"--- Installing {pkg} ---\n")
        f.flush()
        result = subprocess.run(
            [python, "-m", "pip", "install", pkg],
            capture_output=True, text=True
        )
        f.write(result.stdout + "\n")
        if result.stderr:
            f.write(f"STDERR: {result.stderr}\n")
        f.write(f"Exit code: {result.returncode}\n\n")
        f.flush()

    # Verify
    f.write("=== VERIFICATION ===\n")
    for pkg_name in ["warp", "trimesh", "numpy", "scipy", "matplotlib", "shapely"]:
        try:
            result = subprocess.run(
                [python, "-c", f"import {pkg_name}; print('{pkg_name} OK')"],
                capture_output=True, text=True
            )
            f.write(f"{result.stdout.strip()}\n")
        except Exception as e:
            f.write(f"{pkg_name} FAILED: {e}\n")
        f.flush()

print(f"Install log: {log_path}")

