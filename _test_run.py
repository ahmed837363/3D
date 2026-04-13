"""Quick test: run blender_script.py with minimal params to verify shrinkwrap works."""
import subprocess
import json
import os
import sys
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
BLENDER_PATH = (
    os.environ.get("BLENDER_PATH")
    or shutil.which("blender")
    or r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
)
SCRIPT_PATH = os.path.join(BASE_DIR, "blender_script.py")
try:
    MAX_ATTEMPTS = max(1, int(os.environ.get("TEST_MAX_ATTEMPTS", "3")))
except ValueError:
    MAX_ATTEMPTS = 3

output_path = os.path.join(OUTPUT_DIR, "_test_shrinkwrap.png")
blend_path = os.path.join(OUTPUT_DIR, "_test_shrinkwrap.blend")

# Clean old test files
for f in [output_path, blend_path]:
    if os.path.exists(f):
        os.remove(f)

params = {
    "fabric_color": "#1a1a2e",
    "fabric_type": "chiffon",
    "pattern": "none",
    "pattern_color": "#c4a35a",
    "pattern_scale": 5.0,
    "drape_quality": 10,
    "render_samples": 32,
    "render_engine": "EEVEE",
    "output_path": output_path,
    "texture_path": "",
    "blend_path": blend_path,
    "open_in_blender": False,
    "cloth_params": {
        "mass": 0.15,
        "tension_stiffness": 2.5,
        "compression_stiffness": 0.0,
        "bending_stiffness": 0.001,
        "tension_damping": 2.5,
        "compression_damping": 0.0,
        "bending_damping": 0.5,
        "friction": 5.0,
        "self_friction": 5.0,
        "collision_distance": 0.001,
        "self_collision_distance": 0.001,
        "collision_quality": 2,
        "quality_steps": 5,
        "roughness": 0.7,
        "sheen": 0.0,
        "transmission": 0.0,
    },
    "pattern_source": "freesewing",
    "garment_size": "M",
    "garment_height": 165.0,
    # Warp XPBD draper (external physics engine)
    "use_warp_draper": True,
    "warp_params": {
        "mass": 0.15,
        "stretch_compliance": 0.0004,
        "bend_compliance": 1.0,
        "collision_margin": 0.003,
        "damping": 0.02,
        "sewing_stiffness": 10.0,
    },
}

log_path = os.path.join(BASE_DIR, "_test_log.txt")

cmd = [
    BLENDER_PATH,
    "--background",
    "--python", SCRIPT_PATH,
    "--", json.dumps(params),
]

# Use unbuffered env
env = os.environ.copy()
env["PYTHONUNBUFFERED"] = "1"

def blender_exists(path: str) -> bool:
    """Return True if path is an existing Blender executable path or command on PATH."""
    if not path:
        return False
    altsep = os.path.altsep or ""
    is_path_like = os.path.isabs(path) or os.path.sep in path or (altsep and altsep in path)
    if is_path_like:
        return os.path.exists(path)
    return shutil.which(path) is not None

attempts_used = 0
proc = None

with open(log_path, "w", buffering=1) as log_file:
    log_file.write(f"Starting Blender test (max attempts: {MAX_ATTEMPTS})...\n")
    log_file.write(f"Blender path: {BLENDER_PATH}\n")
    log_file.flush()

    if not blender_exists(BLENDER_PATH):
        log_file.write("Blender executable not found. Skipping integration run.\n")
    else:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            attempts_used = attempt
            log_file.write(f"\n--- Attempt {attempt}/{MAX_ATTEMPTS} ---\n")
            log_file.flush()
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            proc.wait()
            log_file.write(f"Exit code: {proc.returncode}\n")
            log_file.flush()

            if proc.returncode == 0 and os.path.exists(output_path):
                break

# Check result
result_path = os.path.join(BASE_DIR, "_test_result.txt")
with open(result_path, "w") as rf:
    if proc is None:
        rf.write("SKIPPED: Blender executable not found.\n")
    elif os.path.exists(output_path):
        rf.write(f"SUCCESS: Rendered to {output_path}\n")
        rf.write(f"Blend: {blend_path}\n")
        size = os.path.getsize(output_path)
        rf.write(f"PNG size: {size} bytes\n")
        rf.write(f"Attempts used: {attempts_used}\n")
    else:
        rf.write(f"FAILED: No output at {output_path} after {MAX_ATTEMPTS} attempts\n")
        # Show error lines from log
        with open(log_path, "r") as lf:
            for line in lf:
                if any(k in line.lower() for k in ["error", "traceback", "exception"]):
                    rf.write(f"  ERR: {line}")
    if proc is not None:
        rf.write(f"\nBlender exit code: {proc.returncode}\n")
