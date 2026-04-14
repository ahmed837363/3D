import subprocess
import json
import os

blender_path = r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
script_path = "blender_script.py"

params = {
    "fabric_color": "#1a1a2e",
    "fabric_type": "silk",
    "pattern": "none",
    "pattern_color": "#c4a35a",
    "pattern_scale": 5.0,
    "drape_quality": 5,
    "render_samples": 32,
    "render_engine": "BLENDER_EEVEE",
    "output_path": os.path.abspath("test_result_abaya.png"),
    "texture_path": "",
    "blend_path": os.path.abspath("test_result_abaya.blend"),
    "open_in_blender": False,
    "cloth_params": {
        "quality_steps": 5,
        "mass": 0.15,
        "tension_stiffness": 15,
        "bending_stiffness": 0.5,
        "tension_damping": 5,
        "bending_damping": 0.5,
        "compression_stiffness": 15,
        "compression_damping": 5,
        "friction": 5,
        "collision_quality": 5,
        "self_friction": 5,
        "roughness": 0.4,
        "sheen": 0.2,
        "transmission": 0
    },
    "pattern_source": "procedural",
    "garment_size": "M",
    "garment_height": 165.0,
    "use_warp_draper": False
}

cmd = [
    blender_path,
    "--background",
    "--python", script_path,
    "--", json.dumps(params)
]

print(f"Running command: {' '.join(cmd)}")
result = subprocess.run(cmd, capture_output=True, text=True)
print(result.stdout)
print(result.stderr)
if result.returncode == 0:
    print("SUCCESS")
else:
    print("FAILED")
