"""Quick test: run blender_script.py with minimal params to verify shrinkwrap works."""
import subprocess
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
BLENDER_PATH = r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
SCRIPT_PATH = os.path.join(BASE_DIR, "blender_script.py")

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

with open(log_path, "w", buffering=1) as log_file:
    log_file.write("Starting Blender test...\n")
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
    log_file.write(f"\nExit code: {proc.returncode}\n")
    log_file.flush()

# Check result
result_path = os.path.join(BASE_DIR, "_test_result.txt")
with open(result_path, "w") as rf:
    if os.path.exists(output_path):
        rf.write(f"SUCCESS: Rendered to {output_path}\n")
        rf.write(f"Blend: {blend_path}\n")
        size = os.path.getsize(output_path)
        rf.write(f"PNG size: {size} bytes\n")
    else:
        rf.write(f"FAILED: No output at {output_path}\n")
        # Show error lines from log
        with open(log_path, "r") as lf:
            for line in lf:
                if any(k in line.lower() for k in ["error", "traceback", "exception"]):
                    rf.write(f"  ERR: {line}")
    rf.write(f"\nBlender exit code: {proc.returncode}\n")
