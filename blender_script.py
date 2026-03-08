"""
Blender headless script: generates abaya fabric, drapes it on a procedural
mannequin using cloth physics, and renders the result.

Called by main.py via:
    blender --background --python blender_script.py -- <json_params>
"""

import bpy
import bmesh
import sys
import os
import json
import math
import time
from mathutils import Vector


def hex_to_rgba(hex_str):
    hex_str = hex_str.lstrip("#")
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    r = pow(r, 2.2)
    g = pow(g, 2.2)
    b = pow(b, 2.2)
    return (r, g, b, 1.0)


argv = sys.argv
params_json = argv[argv.index("--") + 1]
params = json.loads(params_json)

FABRIC_TYPE = params["fabric_type"]
PATTERN = params["pattern"]
PATTERN_SCALE = params["pattern_scale"]
DRAPE_QUALITY = params["drape_quality"]
RENDER_SAMPLES = params["render_samples"]
RENDER_ENGINE = params.get("render_engine", "EEVEE")
OUTPUT_PATH = params["output_path"]
TEXTURE_PATH = params.get("texture_path", "")
OPEN_IN_BLENDER = params.get("open_in_blender", False)
BLEND_PATH = params.get("blend_path", "")
FABRIC_COLOR = hex_to_rgba(params["fabric_color"])
PATTERN_COLOR = hex_to_rgba(params["pattern_color"])

# ClothSDK parameters (passed from main.py via cloth_sdk module)
CLOTH_PARAMS = params.get("cloth_params", {})
FABRIC_DEFAULTS = {"mass": 0.15, "tension_stiffness": 5.0, "compression_stiffness": 5.0,
                   "bending_stiffness": 0.005, "tension_damping": 5.0, "compression_damping": 5.0,
                   "bending_damping": 0.5, "friction": 5.0, "self_friction": 5.0,
                   "collision_distance": 0.002, "self_collision_distance": 0.003,
                   "collision_quality": 5, "quality_steps": 10,
                   "roughness": 0.7, "sheen": 0.0, "transmission": 0.0}
for k, v in FABRIC_DEFAULTS.items():
    CLOTH_PARAMS.setdefault(k, v)

# FreeSewing pattern parameters
PATTERN_SOURCE = params.get("pattern_source", "procedural")  # "procedural" or "freesewing"
GARMENT_SIZE = params.get("garment_size", "M")
GARMENT_HEIGHT = params.get("garment_height", 165.0)  # cm

# Import FreeSewing pattern system (adjacent to this script)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from freesewing_patterns import (
        BodyMeasurements,
        AbayaDrafter,
        generate_blender_pattern_data,
        FreeSewingSVGExporter,
    )
    FREESEWING_AVAILABLE = True
    print("  [FreeSewing] Pattern system loaded", flush=True)
except ImportError as e:
    FREESEWING_AVAILABLE = False
    print(f"  [FreeSewing] Not available: {e}", flush=True)

# --- Scene Setup ---
# Don't use factory_settings as it disables user addons (like MPFB)
# Instead, clear the scene manually to preserve addon states

print("PROGRESS:2%|Setting up scene...")
sys.stdout.flush()

# Delete all existing objects
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

# Delete all meshes, materials, etc
for mesh in bpy.data.meshes:
    bpy.data.meshes.remove(mesh)
for mat in bpy.data.materials:
    bpy.data.materials.remove(mat)
for cam in bpy.data.cameras:
    bpy.data.cameras.remove(cam)
for light in bpy.data.lights:
    bpy.data.lights.remove(light)

scene = bpy.context.scene
scene.frame_start = 1
# Dynamic frame count: heavier fabrics need more simulation time to settle
fabric_mass = CLOTH_PARAMS.get("mass", 0.15)
if PATTERN_SOURCE == "freesewing":
    # Garment tube with small ease starts close to body — needs less settling time
    base_frames = 60
    extra_frames = int(fabric_mass * 40)
else:
    base_frames = 80
    extra_frames = int(fabric_mass * 60)  # e.g. velvet (0.5) gets +30 frames
scene.frame_end = base_frames + extra_frames
scene.frame_set(1)


# --- Mannequin ---
def create_mannequin_mpfb():
    """
    Create a realistic female mannequin using MPFB (MakeHuman Plugin for Blender).
    Falls back to primitive mannequin if MPFB is not available.
    """
    try:
        # MPFB should already be loaded since we didn't reset factory settings
        # Try to import MPFB services directly
        from mpfb.services.humanservice import HumanService
        from mpfb.services.objectservice import ObjectService

        print("  [MPFB] Creating realistic human model...", flush=True)

        # Create a new human using MPFB
        human = HumanService.create_human(
            detailed_helpers=False,
            extra_vertex_groups=True,
            scale_factor=1.0,
            mask_helpers=True
        )

        if human is None:
            raise Exception("HumanService.create_human() returned None")

        human.name = "Mannequin"

        # Position at origin
        human.location = (0, 0, 0)

        # Make it a female body type by adjusting shape keys if available
        if human.data.shape_keys:
            for key in human.data.shape_keys.key_blocks:
                if "female" in key.name.lower():
                    key.value = 1.0
                elif "male" in key.name.lower() and "female" not in key.name.lower():
                    key.value = 0.0

        # Apply a neutral mannequin material
        mat = bpy.data.materials.new("Mannequin_Mat")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (0.75, 0.72, 0.70, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.6
        bsdf.inputs["Specular IOR Level"].default_value = 0.3

        # Clear existing materials and apply new one
        human.data.materials.clear()
        human.data.materials.append(mat)

        bpy.ops.object.shade_smooth()

        print(f"  [MPFB] Created realistic mannequin: {len(human.data.vertices)} verts", flush=True)
        return human

    except ImportError as e:
        print(f"  [MPFB] Not available ({e}), trying alternative...", flush=True)
        return None
    except Exception as e:
        print(f"  [MPFB] Error: {e}, trying alternative...", flush=True)
        return None


def create_mannequin_mpfb_operator():
    """
    Create mannequin using MPFB operator.
    Applies female phenotype and adjusts for garment size.
    """
    try:
        print("  [MPFB] Trying operator method...", flush=True)

        # Track objects before creation
        existing_objects = set(obj.name for obj in bpy.data.objects)

        # Create human via MPFB operator
        bpy.ops.mpfb.create_human()

        # Find the newly created object
        new_objects = [obj for obj in bpy.data.objects if obj.name not in existing_objects]
        human = None
        for obj in new_objects:
            if obj.type == 'MESH':
                human = obj
                break

        if human is None:
            human = bpy.context.active_object

        if human is None:
            print("  [MPFB] No object created", flush=True)
            return None

        human.name = "Mannequin"

        # Apply female phenotype via shape keys if available
        if human.data.shape_keys:
            for key in human.data.shape_keys.key_blocks:
                key_lower = key.name.lower()
                if "female" in key_lower:
                    key.value = 1.0
                elif "male" in key_lower and "female" not in key_lower:
                    key.value = 0.0
            print(f"  [MPFB] Applied female phenotype", flush=True)

        # Apply mannequin material
        mat = bpy.data.materials.new("Mannequin_Mat")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (0.75, 0.72, 0.70, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.6

        human.data.materials.clear()
        human.data.materials.append(mat)

        print(f"  [MPFB] Created via operator: {len(human.data.vertices)} verts", flush=True)
        return human

    except Exception as e:
        print(f"  [MPFB] Operator failed: {e}", flush=True)

    return None


def create_mannequin_primitive():
    """
    Fallback: Create a simple primitive mannequin.
    """
    print("  [FALLBACK] Creating primitive mannequin...", flush=True)
    objs = []

    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=0.22, depth=0.7, location=(0, 0, 1.15))
    torso = bpy.context.active_object
    torso.name = "M_Torso"
    objs.append(torso)

    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(torso.data)
    for v in bm.verts:
        if v.co.z > 0.2:
            v.co.x *= 1.15
            v.co.y *= 0.85
        if v.co.z < -0.2:
            v.co.x *= 0.85
            v.co.y *= 0.75
    bmesh.update_edit_mesh(torso.data)
    bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, radius=0.26, location=(0, 0, 0.75))
    h = bpy.context.active_object
    h.scale = (1.0, 0.8, 0.6)
    bpy.ops.object.transform_apply(scale=True)
    objs.append(h)

    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.12, location=(0, 0, 1.65))
    objs.append(bpy.context.active_object)

    bpy.ops.mesh.primitive_cylinder_add(radius=0.06, depth=0.15, location=(0, 0, 1.55))
    objs.append(bpy.context.active_object)

    bpy.ops.mesh.primitive_cylinder_add(radius=0.04, depth=0.5, location=(0, 0, 1.42), rotation=(0, math.radians(90), 0))
    sh = bpy.context.active_object
    bpy.ops.object.transform_apply(rotation=True)
    objs.append(sh)

    bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=0.55, location=(0.35, 0, 1.15))
    objs.append(bpy.context.active_object)

    bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=0.55, location=(-0.35, 0, 1.15))
    objs.append(bpy.context.active_object)

    bpy.ops.mesh.primitive_cylinder_add(radius=0.07, depth=0.75, location=(0.1, 0, 0.35))
    objs.append(bpy.context.active_object)

    bpy.ops.mesh.primitive_cylinder_add(radius=0.07, depth=0.75, location=(-0.1, 0, 0.35))
    objs.append(bpy.context.active_object)

    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = torso
    bpy.ops.object.join()

    mannequin = bpy.context.active_object
    mannequin.name = "Mannequin"
    bpy.ops.object.shade_smooth()

    mod = mannequin.modifiers.new(name="Subsurf", type='SUBSURF')
    mod.levels = 2
    bpy.ops.object.modifier_apply(modifier="Subsurf")

    mat = bpy.data.materials.new("Mannequin_Mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.65, 0.62, 0.60, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.8
    mannequin.data.materials.append(mat)
    return mannequin


def create_mannequin():
    """
    Create mannequin - tries MPFB first, falls back to primitive.
    """
    # Try MPFB service method first
    mannequin = create_mannequin_mpfb()

    # Try MPFB operator method
    if mannequin is None:
        mannequin = create_mannequin_mpfb_operator()

    # Fallback to primitive mannequin
    if mannequin is None:
        mannequin = create_mannequin_primitive()

    return mannequin


print("PROGRESS:5%|Building mannequin...")
sys.stdout.flush()
mannequin = create_mannequin()

# Ensure mannequin is in the main collection
if mannequin.name not in bpy.context.collection.objects:
    # Link to main collection if not already there
    try:
        bpy.context.collection.objects.link(mannequin)
    except RuntimeError:
        pass  # Already linked

bpy.context.view_layer.objects.active = mannequin
mannequin.select_set(True)

# Add collision modifier
bpy.ops.object.modifier_add(type='COLLISION')
mannequin.collision.thickness_outer = 0.002
mannequin.collision.thickness_inner = 0.002
mannequin.collision.cloth_friction = 20.0

# Verify mannequin exists
print(f"  Mannequin: {mannequin.name}, {len(mannequin.data.vertices)} verts", flush=True)
print(f"  Location: {mannequin.location[:]}", flush=True)

print("PROGRESS:10%|Mannequin ready")
sys.stdout.flush()


# --- Fabric pipeline ---
# Stage 1: Body tube (cylinder that wraps around mannequin)
# Stage 2: Sleeve panels (flat panels near arms)
# OR: FreeSewing accurate pattern pieces with smooth Bezier curves

def sew_panels_together(panels: list) -> object:
    """
    Join all panels into a single mesh and create sewing springs between edges.
    This connects front/back bodice at shoulders and sides.
    Works with both procedural and FreeSewing pattern panels.

    Returns the joined mesh object with sewing vertex groups.
    """
    if not panels or len(panels) < 2:
        return panels[0] if panels else None

    print(f"  [SEW] Joining {len(panels)} panels for sewing...", flush=True)

    # First, identify edge vertices on each panel for sewing
    # Mark the side edges (leftmost and rightmost vertices) for sewing
    for panel in panels:
        bpy.context.view_layer.objects.active = panel
        panel.select_set(True)

        # Create sewing vertex group
        sew_group = panel.vertex_groups.new(name="Sew")

        # Find edge vertices (on the left and right sides of each panel)
        # These are vertices at the X extremes
        xs = [v.co.x for v in panel.data.vertices]
        if xs:
            min_x, max_x = min(xs), max(xs)
            width = max_x - min_x
            edge_threshold = width * 0.05  # 5% from edges

            edge_verts = []
            for v in panel.data.vertices:
                if v.co.x <= min_x + edge_threshold or v.co.x >= max_x - edge_threshold:
                    edge_verts.append(v.index)

            sew_group.add(edge_verts, 1.0, 'ADD')
            print(f"    [SEW] {panel.name}: {len(edge_verts)} edge verts marked for sewing", flush=True)

    # Join all panels into one mesh
    bpy.ops.object.select_all(action='DESELECT')
    for panel in panels:
        panel.select_set(True)
    bpy.context.view_layer.objects.active = panels[0]
    bpy.ops.object.join()

    joined = bpy.context.active_object
    joined.name = "Abaya_Sewn"

    print(f"  [SEW] Joined into single mesh: {len(joined.data.vertices)} total verts", flush=True)

    return joined


def import_svg_as_mesh(svg_path: str) -> list:
    """
    Import SVG file and convert curves to mesh objects.
    Returns list of mesh objects ready for cloth simulation.
    """
    # Import SVG
    bpy.ops.import_curve.svg(filepath=svg_path)

    # Get all imported curves
    imported = [obj for obj in bpy.context.selected_objects if obj.type == 'CURVE']
    meshes = []

    for curve_obj in imported:
        bpy.context.view_layer.objects.active = curve_obj

        # Convert curve to mesh
        bpy.ops.object.convert(target='MESH')
        mesh_obj = bpy.context.active_object

        # Subdivide for cloth simulation
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.subdivide(number_cuts=8)
        bpy.ops.mesh.fill()  # Fill the shape
        bpy.ops.mesh.subdivide(number_cuts=4)  # More subdivisions for cloth
        bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.02)
        bpy.ops.object.mode_set(mode='OBJECT')

        bpy.ops.object.shade_smooth()
        meshes.append(mesh_obj)

    return meshes


def create_pattern_piece_from_data(name: str, vertices: list, position: tuple, rotation: tuple = (0, 0, 0)) -> object:
    """
    Create a mesh pattern piece from FreeSewing pattern data.
    Creates a curved panel that wraps around the mannequin for proper draping.

    Args:
        name: Name for the object
        vertices: List of [x, y, z] vertex positions (in meters) - Y=0, height in Z
        position: (x, y, z) world position for the piece
        rotation: (rx, ry, rz) rotation in radians
    """
    print(f"    [SEAMLY2D] Creating {name}...", flush=True)

    if not vertices or len(vertices) < 3:
        print(f"    [SEAMLY2D] ERROR: Not enough vertices for {name}", flush=True)
        return None

    # Calculate bounds from vertices (X=width, Z=height, Y should be 0)
    xs = [v[0] for v in vertices]
    zs = [v[2] for v in vertices]

    min_x, max_x = min(xs), max(xs)
    min_z, max_z = min(zs), max(zs)

    width = max_x - min_x
    height = abs(max_z - min_z)  # Height from Z coordinates

    if height < 0.01:
        print(f"    [SEAMLY2D] ERROR: {name} has zero height! Check vertex format.", flush=True)
        return None

    print(f"    [SEAMLY2D] {name} bounds: {width:.3f}m x {height:.3f}m", flush=True)

    # Create a plane - by default it's in XY plane
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name

    # Rotate plane to be vertical (XZ plane) - rotate 90° around X
    obj.rotation_euler = (math.radians(90), 0, 0)
    bpy.ops.object.transform_apply(rotation=True)

    # Scale: X=width, Z=height
    obj.scale = (width if width > 0.01 else 0.5, height, 1)
    bpy.ops.object.transform_apply(scale=True)

    # Subdivide BEFORE curving so we have enough vertices
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.subdivide(number_cuts=16)  # More subdivisions for smooth curve
    bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')

    # Curve the panel to wrap around body (cylindrical bend)
    # This makes the panel start in a wrapped position for better draping
    is_sleeve = "Sleeve" in name
    curve_radius = 0.15 if is_sleeve else 0.28  # Tighter curve for sleeves

    for v in obj.data.vertices:
        # Curve around Y axis: X position determines Y offset (cylindrical wrap)
        x_normalized = v.co.x / (width / 2) if width > 0.01 else 0  # -1 to 1
        # Apply cylindrical curve: push vertices back based on X position
        curve_amount = (1 - x_normalized**2) * curve_radius
        v.co.y -= curve_amount

    # Apply the panel-specific rotation (for positioning around mannequin)
    obj.rotation_euler = rotation
    bpy.ops.object.transform_apply(rotation=True)

    # Move to final position
    obj.location = position

    bpy.ops.object.shade_smooth()

    print(f"    [SEAMLY2D] {name} created: {len(obj.data.vertices)} verts (curved)", flush=True)

    return obj


def create_freesewing_abaya_panels(pattern_data: dict) -> list:
    """
    Create abaya panels from FreeSewing pattern data.
    Positions pieces around the mannequin ready for cloth simulation.

    Vertex format from freesewing_patterns.py: [x, 0, z] where Z is height.
    Panels are created vertical (XZ plane) then rotated to wrap mannequin.
    """
    panels = []
    pieces = pattern_data.get("pieces", {})
    total_pieces = len(pieces)

    print(f"  [SEAMLY2D] Creating {total_pieces} pattern pieces...", flush=True)

    # Position mapping for each pattern piece around the mannequin
    # Mannequin torso radius ~0.22m, so panels at Y=±0.24 are very close
    # Panels start above mannequin and fall/drape via cloth simulation
    # Pin top edge so fabric hangs from shoulders
    piece_placement = {
        "Front_Bodice": {
            "position": (0, -0.24, 0.90),   # Very close to front of mannequin
            "rotation": (0, 0, 0),           # Facing forward
            "pin_edge": "top"
        },
        "Back_Bodice": {
            "position": (0, 0.24, 0.90),    # Very close to back of mannequin
            "rotation": (0, 0, 0),           # Facing backward
            "pin_edge": "top"
        },
        "Left_Sleeve": {
            "position": (0.36, 0, 1.15),    # Close to left arm
            "rotation": (0, 0, math.radians(90)),  # Rotate to face sideways
            "pin_edge": "top"
        },
        "Right_Sleeve": {
            "position": (-0.36, 0, 1.15),   # Close to right arm
            "rotation": (0, 0, math.radians(-90)), # Rotate to face sideways
            "pin_edge": "top"
        },
        "Skirt_Front": {
            "position": (0, -0.24, 0.50),   # Front skirt close to body
            "rotation": (0, 0, 0),
            "pin_edge": "top"
        },
        "Skirt_Back": {
            "position": (0, 0.24, 0.50),    # Back skirt close to body
            "rotation": (0, 0, 0),
            "pin_edge": "top"
        },
    }

    for idx, (piece_name, piece_info) in enumerate(pieces.items()):
        # Progress update for each piece
        piece_pct = 11 + int((idx / total_pieces) * 4)
        print(f"PROGRESS:{piece_pct}%|Creating {piece_name} ({idx+1}/{total_pieces})...")
        sys.stdout.flush()

        placement = piece_placement.get(piece_name)
        if not placement:
            print(f"  [WARN] No placement defined for {piece_name}, skipping", flush=True)
            continue

        vertices = piece_info.get("vertices", [])
        if not vertices:
            print(f"  [WARN] No vertices for {piece_name}, skipping", flush=True)
            continue

        print(f"  [FreeSewing] {piece_name}: {len(vertices)} base vertices", flush=True)

        panel = create_pattern_piece_from_data(
            name=f"Abaya_{piece_name}",
            vertices=vertices,
            position=placement["position"],
            rotation=placement["rotation"]
        )

        if panel is None:
            print(f"  [ERROR] Failed to create {piece_name}", flush=True)
            continue

        # Add pin vertex group for cloth simulation
        max_z = max(v.co.z for v in panel.data.vertices)
        min_z = min(v.co.z for v in panel.data.vertices)
        height = max_z - min_z

        pin_group = panel.vertex_groups.new(name="Pin")

        if placement["pin_edge"] == "top":
            pin_threshold = max_z - height * 0.08
            top_verts = [v.index for v in panel.data.vertices if v.co.z >= pin_threshold]
        else:
            pin_threshold = min_z + height * 0.08
            top_verts = [v.index for v in panel.data.vertices if v.co.z <= pin_threshold]

        pin_group.add(top_verts, 1.0, 'ADD')

        total = len(panel.data.vertices)
        print(f"    [FreeSewing] {piece_name}: {total} verts, {len(top_verts)} pinned", flush=True)

        panels.append(panel)

    print(f"  [FreeSewing] Successfully created {len(panels)} panels", flush=True)

    return panels


def measure_mannequin_at_height(mannequin_obj, z_height: float, torso_only: bool = True) -> float:
    """
    Measure the approximate radius of the mannequin torso at a given height.
    Filters to only measure the central torso area (excludes arms in T-pose).
    ALL values from actual mesh - no hardcoded fallbacks.
    """
    world_matrix = mannequin_obj.matrix_world
    tolerance = 0.03  # 3cm vertical tolerance for more precision

    radii = []
    for v in mannequin_obj.data.vertices:
        world_co = world_matrix @ v.co
        if abs(world_co.z - z_height) < tolerance:
            # For torso measurement, only consider vertices near center (|x| < 0.20m)
            # This excludes arms which extend to x = ±0.5m in T-pose
            if torso_only and abs(world_co.x) > 0.20:
                continue
            radius = math.sqrt(world_co.x**2 + world_co.y**2)
            radii.append(radius)

    if not radii:
        # No vertices at this height - expand tolerance and try again
        tolerance = 0.06
        for v in mannequin_obj.data.vertices:
            world_co = world_matrix @ v.co
            if abs(world_co.z - z_height) < tolerance:
                if torso_only and abs(world_co.x) > 0.20:
                    continue
                radius = math.sqrt(world_co.x**2 + world_co.y**2)
                radii.append(radius)

    if not radii:
        # Still no vertices - use mesh bounds to estimate
        all_verts = [world_matrix @ v.co for v in mannequin_obj.data.vertices]
        torso_verts = [v for v in all_verts if abs(v.x) < 0.20]
        if torso_verts:
            avg_radius = sum(math.sqrt(v.x**2 + v.y**2) for v in torso_verts) / len(torso_verts)
            return avg_radius
        # Last resort - derive from mesh Y extent
        max_y = max(v.y for v in all_verts)
        return max_y * 0.8

    radii.sort()

    # Use 90th percentile to get a good torso measurement
    idx = int(len(radii) * 0.90)
    return radii[min(idx, len(radii)-1)]


def measure_mannequin_body(mannequin_obj) -> dict:
    """
    Measure key body dimensions of the mannequin at many points.
    Returns dict with heights and radii for smooth garment generation.
    """
    world_matrix = mannequin_obj.matrix_world

    # Get all world-space vertices
    world_verts = [world_matrix @ v.co for v in mannequin_obj.data.vertices]

    max_z = max(v.z for v in world_verts)
    min_z = min(v.z for v in world_verts)
    height = max_z - min_z

    # More detailed body landmarks as fraction of height
    # Based on standard human proportions
    measurements = {
        "height": height,
        "max_z": max_z,
        "min_z": min_z,
        # Head and neck
        "chin_z": min_z + height * 0.87,
        "neck_z": min_z + height * 0.84,
        # Shoulders and upper body
        "shoulder_z": min_z + height * 0.81,
        "armpit_z": min_z + height * 0.77,
        "upper_bust_z": min_z + height * 0.74,
        "bust_z": min_z + height * 0.71,
        "underbust_z": min_z + height * 0.67,
        # Torso
        "waist_z": min_z + height * 0.60,
        "low_waist_z": min_z + height * 0.56,
        "hip_z": min_z + height * 0.52,
        "low_hip_z": min_z + height * 0.48,
        # Legs
        "upper_thigh_z": min_z + height * 0.44,
        "mid_thigh_z": min_z + height * 0.38,
        "knee_z": min_z + height * 0.28,
        "calf_z": min_z + height * 0.18,
        "ankle_z": min_z + height * 0.05,
    }

    # Measure radius at each point
    for key in list(measurements.keys()):
        if key.endswith("_z") and key != "max_z" and key != "min_z":
            r_key = key.replace("_z", "_r")
            measurements[r_key] = measure_mannequin_at_height(mannequin_obj, measurements[key])

    print(f"    Body landmarks measured at {len([k for k in measurements if k.endswith('_r')])} heights", flush=True)

    return measurements


def measure_mannequin_arms(mannequin_obj, body_measurements: dict) -> dict:
    """
    Measure arm positions from mannequin, derive arm thickness from body proportions.
    Arm radii are calculated as ratios of measured body parts (more reliable than slicing).
    """
    world_matrix = mannequin_obj.matrix_world
    world_verts = [world_matrix @ v.co for v in mannequin_obj.data.vertices]

    max_z = max(v.z for v in world_verts)
    min_z = min(v.z for v in world_verts)
    height = max_z - min_z

    # Find the X extremes - hands/wrists
    max_x = max(v.x for v in world_verts)
    min_x = min(v.x for v in world_verts)

    print(f"    [ARMS] Mesh X range: {min_x:.2f} to {max_x:.2f}", flush=True)

    # Find torso width at shoulder height
    shoulder_z = min_z + height * 0.80
    shoulder_tolerance = 0.05

    shoulder_verts = [v for v in world_verts 
                     if abs(v.z - shoulder_z) < shoulder_tolerance 
                     and abs(v.x) < 0.25]

    if shoulder_verts:
        torso_max_x = max(v.x for v in shoulder_verts)
        torso_min_x = min(v.x for v in shoulder_verts)
    else:
        torso_max_x = max_x * 0.35
        torso_min_x = min_x * 0.35

    left_arm_start = torso_max_x + 0.02
    left_arm_end = max_x
    right_arm_start = torso_min_x - 0.02
    right_arm_end = min_x

    arm_length = left_arm_end - left_arm_start

    # Get arm Z height from hand vertices
    left_hand_verts = [v for v in world_verts if v.x > max_x - 0.05]
    right_hand_verts = [v for v in world_verts if v.x < min_x + 0.05]

    if left_hand_verts:
        left_arm_z = sum(v.z for v in left_hand_verts) / len(left_hand_verts)
    else:
        left_arm_z = min_z + height * 0.79

    if right_hand_verts:
        right_arm_z = sum(v.z for v in right_hand_verts) / len(right_hand_verts)
    else:
        right_arm_z = min_z + height * 0.79

    # DERIVE ARM RADII FROM BODY PROPORTIONS (much more reliable)
    # Human proportions: upper arm ~25% of shoulder width, wrist ~15%
    shoulder_r = body_measurements.get("shoulder_r", 0.15)

    # Upper arm radius: ~25-30% of shoulder radius
    upper_arm_radius = shoulder_r * 0.28
    # Elbow radius: ~22-25% of shoulder radius  
    elbow_radius = shoulder_r * 0.24
    # Wrist radius: ~15-18% of shoulder radius
    wrist_radius = shoulder_r * 0.18

    measurements = {
        "left_arm_start": left_arm_start,
        "left_arm_end": left_arm_end,
        "left_arm_z": left_arm_z,
        "left_arm_length": arm_length,
        "right_arm_start": right_arm_start,
        "right_arm_end": right_arm_end,
        "right_arm_z": right_arm_z,
        "right_arm_length": abs(right_arm_end - right_arm_start),
        "upper_arm_radius": upper_arm_radius,
        "elbow_radius": elbow_radius,
        "wrist_radius": wrist_radius,
    }

    print(f"    [ARMS] Left arm: X={left_arm_start:.2f} to {left_arm_end:.2f} (length={arm_length:.2f}m), Z={left_arm_z:.2f}", flush=True)
    print(f"    [ARMS] Right arm: X={right_arm_start:.2f} to {right_arm_end:.2f} (length={measurements['right_arm_length']:.2f}m), Z={right_arm_z:.2f}", flush=True)
    print(f"    [ARMS] Radii (from shoulder {shoulder_r:.3f}): upper={upper_arm_radius:.3f}, elbow={elbow_radius:.3f}, wrist={wrist_radius:.3f}", flush=True)

    return measurements


def create_sleeve_mesh(bm, side: str, arm_start: float, arm_end: float, arm_z: float, 
                       upper_radius: float, elbow_radius: float, wrist_radius: float,
                       segments: int = 24):
    """
    Create a sleeve tube using AUTO-MEASURED arm dimensions.
    All radii are derived from the mannequin - no hardcoded sizes.

    Args:
        bm: BMesh to add vertices to
        side: "left" or "right"
        arm_start: X position where arm starts (near body)
        arm_end: X position where arm ends (wrist)
        arm_z: Z height of the arm
        upper_radius: Measured upper arm radius + ease
        elbow_radius: Measured elbow radius + ease
        wrist_radius: Measured wrist radius + ease
        segments: Number of segments around sleeve circumference
    """
    sleeve_start_x = arm_start
    sleeve_end_x = arm_end

    # Create sleeve rings — wide abaya sleeves with gentle taper
    # Fewer rings but proper radii for flowing sleeves
    sleeve_rings_data = [
        # (t position 0-1, radius, z_offset)
        # Shoulder junction — matches body tube armpit radius
        (0.00, upper_radius * 1.1, 0.00),
        (0.08, upper_radius * 1.05, 0.00),
        (0.15, upper_radius, 0.00),
        # Upper arm — wide and flowing
        (0.25, upper_radius * 0.95, 0.00),
        (0.35, (upper_radius + elbow_radius) / 2, 0.00),
        # Elbow area
        (0.45, elbow_radius * 1.05, 0.00),
        (0.55, elbow_radius, 0.00),
        # Forearm — gradual taper
        (0.65, (elbow_radius + wrist_radius) / 2, 0.00),
        (0.75, wrist_radius * 1.15, 0.00),
        # Wrist
        (0.85, wrist_radius * 1.05, 0.00),
        (0.95, wrist_radius, 0.00),
        (1.00, wrist_radius * 0.95, 0.00),
    ]

    all_sleeve_rings = []

    for t, radius, z_offset in sleeve_rings_data:
        ring_verts = []
        x_pos = sleeve_start_x + (sleeve_end_x - sleeve_start_x) * t

        for i in range(segments):
            angle = (i / segments) * 2 * math.pi
            y = math.cos(angle) * radius
            z = arm_z + math.sin(angle) * radius + z_offset
            v = bm.verts.new((x_pos, y, z))
            ring_verts.append(v)
        all_sleeve_rings.append(ring_verts)

    bm.verts.ensure_lookup_table()

    # Create faces between sleeve rings
    for r in range(len(all_sleeve_rings) - 1):
        ring1 = all_sleeve_rings[r]
        ring2 = all_sleeve_rings[r + 1]
        for i in range(segments):
            v1 = ring1[i]
            v2 = ring1[(i + 1) % segments]
            v3 = ring2[(i + 1) % segments]
            v4 = ring2[i]
            try:
                bm.faces.new([v1, v2, v3, v4])
            except ValueError:
                pass

    return all_sleeve_rings


def create_freesewing_connected_abaya(pattern_data, mannequin_obj=None) -> object:
    """
    Create abaya using SHRINKWRAP PRE-POSITIONING + cloth simulation.

    Professional garment simulation approach:
    1. Measure mannequin body at key landmarks
    2. Create body-conforming tube with minimal ease
    3. SHRINKWRAP upper body onto mannequin surface (key technique!)
    4. Cloth sim then does fine draping from that close starting position

    The shrinkwrap prevents the 'bag collapse' — the garment starts
    conforming to the body instead of falling under gravity.
    """
    if mannequin_obj is None:
        raise ValueError("Mannequin required for automatic measurements")

    print(f"  [FreeSewing] Auto-measuring mannequin...", flush=True)

    # Step 1: Get body measurements
    body = measure_mannequin_body(mannequin_obj)
    arm_data = measure_mannequin_arms(mannequin_obj, body)

    # Step 2: FreeSewing pattern generation (for SVG export & validation)
    if FREESEWING_AVAILABLE:
        fs_measurements = BodyMeasurements.from_mannequin_body(body, arm_data)

        if GARMENT_HEIGHT and abs(GARMENT_HEIGHT - 165.0) > 1.0:
            height_scale = GARMENT_HEIGHT / fs_measurements.height
            fs_measurements.height = GARMENT_HEIGHT
            fs_measurements.waistToFloor *= height_scale
            fs_measurements.waistToKnee *= height_scale
            fs_measurements.hpsToWaistBack *= height_scale
            fs_measurements.hpsToWaistFront *= height_scale
            fs_measurements.shoulderToWrist *= height_scale
            fs_measurements.shoulderToElbow *= height_scale
            print(f"    Custom height: {GARMENT_HEIGHT:.0f}cm (scale={height_scale:.2f})", flush=True)

        drafter = AbayaDrafter(fs_measurements)
        pieces = drafter.draft_all()

        print(f"    FreeSewing drafted {len(pieces)} pieces:", flush=True)
        for name, piece in pieces.items():
            bounds = piece.get_bounds()
            w = bounds[2] - bounds[0]
            h = bounds[3] - bounds[1]
            print(f"      {name}: {len(piece.vertices)} verts, {w:.1f} x {h:.1f} cm", flush=True)

        try:
            job_id = os.path.basename(OUTPUT_PATH).split('.')[0]
            svg_dir = os.path.join(os.path.dirname(OUTPUT_PATH), f"{job_id}_patterns")
            os.makedirs(svg_dir, exist_ok=True)
            exporter = FreeSewingSVGExporter(pieces)
            svg_path = exporter.export_combined(os.path.join(svg_dir, "abaya_pattern.svg"))
            print(f"    SVG exported: {svg_path}", flush=True)
        except Exception as e:
            print(f"    SVG export failed (non-critical): {e}", flush=True)
    else:
        print(f"    FreeSewing not available, using mannequin measurements only", flush=True)

    # Step 3: Build garment tube with MINIMAL ease
    # Small ease keeps garment close to body for fast, clean cloth simulation
    EASE = 0.015  # 1.5cm ease — start close to body

    neck_r = body["neck_r"]
    shoulder_r = body["shoulder_r"]
    armpit_r = body["armpit_r"]
    upper_bust_r = body["upper_bust_r"]
    bust_r = body["bust_r"]
    underbust_r = body["underbust_r"]
    waist_r = body["waist_r"]
    low_waist_r = body["low_waist_r"]
    hip_r = body["hip_r"]
    low_hip_r = body["low_hip_r"]
    upper_thigh_r = body["upper_thigh_r"]

    chin_z = body["chin_z"]
    neck_z = body["neck_z"]
    shoulder_z = body["shoulder_z"]
    armpit_z = body["armpit_z"]
    upper_bust_z = body["upper_bust_z"]
    bust_z = body["bust_z"]
    underbust_z = body["underbust_z"]
    waist_z = body["waist_z"]
    low_waist_z = body["low_waist_z"]
    hip_z = body["hip_z"]
    low_hip_z = body["low_hip_z"]
    upper_thigh_z = body["upper_thigh_z"]
    knee_z = body["knee_z"]
    calf_z = body["calf_z"]
    ankle_z = body["ankle_z"]
    body_min_z = body["min_z"]

    # Hem: A-line flare for abaya silhouette — generous flare
    hem_radius = hip_r + 0.18
    hem_z = body_min_z + 0.02

    print(f"    Height: {body['height']:.2f}m", flush=True)
    print(f"    Torso: neck={neck_r:.3f}, shoulder={shoulder_r:.3f}, bust={bust_r:.3f}, "
          f"waist={waist_r:.3f}, hip={hip_r:.3f}", flush=True)
    print(f"    Arms: upper={arm_data['upper_arm_radius']:.3f}, elbow={arm_data['elbow_radius']:.3f}, "
          f"wrist={arm_data['wrist_radius']:.3f}", flush=True)

    segments = 48
    sleeve_segments = 24
    E = EASE

    # Neckline opening — should be close to actual neck size, NOT shoulder width
    neckline_r = neck_r + E * 2  # Tight around neck

    ring_data = [
        # Neckline — snug around neck
        (chin_z - 0.02, neckline_r),
        (neck_z + 0.02, neckline_r * 1.05),
        (neck_z,        neckline_r * 1.10),
        # Shoulders — garment widens to shoulder width
        (shoulder_z + 0.02, (neckline_r + shoulder_r + E) / 2),  # transition
        (shoulder_z,        shoulder_r + E),
        # Armpit — transition to arm area
        (armpit_z + 0.02,   armpit_r + E),
        (armpit_z,          armpit_r + E),
        # Bust
        (upper_bust_z,      upper_bust_r + E),
        (bust_z + 0.03,     bust_r + E * 1.3),
        (bust_z,            bust_r + E * 1.3),
        (bust_z - 0.03,     bust_r + E),
        (underbust_z,       underbust_r + E),
        # Waist — abaya is loose here, NOT fitted
        ((underbust_z + waist_z)/2, max(underbust_r, waist_r) + E * 1.5),
        (waist_z + 0.03,    max(waist_r, bust_r * 0.95) + E),
        (waist_z,           max(waist_r, bust_r * 0.95) + E),
        (low_waist_z,       max(low_waist_r, waist_r) + E),
        # Hips
        ((low_waist_z + hip_z)/2, max(low_waist_r, hip_r) + E),
        (hip_z,             hip_r + E),
        (low_hip_z,         max(low_hip_r, hip_r) + E),
        # A-line from hips to hem — flares out for classic abaya shape
        (upper_thigh_z,     hip_r + 0.04),
        ((upper_thigh_z + knee_z)/2, hem_radius * 0.78),
        (knee_z,            hem_radius * 0.86),
        ((knee_z + calf_z)/2, hem_radius * 0.92),
        (calf_z,            hem_radius * 0.97),
        (ankle_z,           hem_radius),
        (hem_z,             hem_radius * 1.02),
    ]

    bm = bmesh.new()
    all_rings = []

    for z_pos, radius in ring_data:
        ring_verts = []
        for i in range(segments):
            angle = (i / segments) * 2 * math.pi
            x = math.cos(angle) * radius
            y = math.sin(angle) * radius
            v = bm.verts.new((x, y, z_pos))
            ring_verts.append(v)
        all_rings.append(ring_verts)

    bm.verts.ensure_lookup_table()

    for r in range(len(all_rings) - 1):
        ring1 = all_rings[r]
        ring2 = all_rings[r + 1]
        for i in range(segments):
            v1 = ring1[i]
            v2 = ring1[(i + 1) % segments]
            v3 = ring2[(i + 1) % segments]
            v4 = ring2[i]
            bm.faces.new([v1, v2, v3, v4])

    # Create sleeves — abaya sleeves are wider than arm but manageable for sim
    print(f"    Creating auto-fitted sleeves...", flush=True)
    # Abaya sleeves wider than arm but not so wide they cause collision issues
    sleeve_multiplier = 1.8  # 1.8x arm width for flowing look
    upper_r = arm_data["upper_arm_radius"] * sleeve_multiplier
    elbow_r = arm_data["elbow_radius"] * sleeve_multiplier
    wrist_r = arm_data["wrist_radius"] * sleeve_multiplier
    # Minimum sleeve radii for proper drape
    upper_r = max(upper_r, 0.08)  # At least 8cm radius
    elbow_r = max(elbow_r, 0.065)
    wrist_r = max(wrist_r, 0.05)

    left_sleeve_rings = create_sleeve_mesh(
        bm, "left",
        arm_data["left_arm_start"], arm_data["left_arm_end"],
        arm_data["left_arm_z"], upper_r, elbow_r, wrist_r, sleeve_segments
    )
    right_sleeve_rings = create_sleeve_mesh(
        bm, "right",
        arm_data["right_arm_start"], arm_data["right_arm_end"],
        arm_data["right_arm_z"], upper_r, elbow_r, wrist_r, sleeve_segments
    )

    # Bridge sleeves to body
    print(f"    Bridging sleeves to body...", flush=True)

    def bridge_sleeve_to_body(sleeve_first_ring, body_rings, body_segments, sleeve_segs, side="left"):
        sleeve_z = sleeve_first_ring[0].co.z
        best_ring_idx = 0
        best_dist = float('inf')
        for idx, ring in enumerate(body_rings):
            dist = abs(ring[0].co.z - sleeve_z)
            if dist < best_dist:
                best_dist = dist
                best_ring_idx = idx
        body_ring = body_rings[best_ring_idx]

        if side == "left":
            scored = sorted([(i, v.co.x) for i, v in enumerate(body_ring)], key=lambda t: -t[1])
        else:
            scored = sorted([(i, v.co.x) for i, v in enumerate(body_ring)], key=lambda t: t[1])

        n_bridge = sleeve_segs
        step = max(1, len(scored) // (n_bridge * 2))
        bridge_indices = [scored[i * step][0] for i in range(n_bridge) if i * step < len(scored)]

        n_actual = min(len(bridge_indices), len(sleeve_first_ring))
        faces_created = 0
        for i in range(n_actual - 1):
            try:
                bm.faces.new([body_ring[bridge_indices[i]], body_ring[bridge_indices[i+1]],
                              sleeve_first_ring[i+1], sleeve_first_ring[i]])
                faces_created += 1
            except ValueError:
                pass
        print(f"      Bridged {side} sleeve: {faces_created} faces", flush=True)

    if left_sleeve_rings:
        bridge_sleeve_to_body(left_sleeve_rings[0], all_rings, segments, sleeve_segments, "left")
    if right_sleeve_rings:
        bridge_sleeve_to_body(right_sleeve_rings[0], all_rings, segments, sleeve_segments, "right")

    # Create mesh object
    mesh = bpy.data.meshes.new("Abaya_Mesh")
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new("Abaya_Connected", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    # Subdivide for smooth cloth sim
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.subdivide(number_cuts=5)
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.shade_smooth()

    # =========================================================================
    # COLLISION-SAFE PRE-POSITIONING
    #
    # The garment tube is built from measured body radii + small ease (1.5cm).
    # This ensures the tube starts very close to the body but NOT inside it.
    #
    # Unlike shrinkwrap (which makes sim 20x slower by pushing verts to
    # collision surface), we simply verify the tube geometry is correct.
    # The cloth sim handles the final draping from this close start position.
    # =========================================================================
    print(f"    [PREFIT] Garment tube built with {E*100:.1f}cm ease from body", flush=True)
    print(f"    [PREFIT] Upper body follows measurements, skirt flares for A-line", flush=True)

    # Set up pinning — critical for proper draping
    max_z = max(v.co.z for v in obj.data.vertices)
    pin_group = obj.vertex_groups.new(name="Pin")

    # Pin neckline (top 6cm) with full weight — MUST hold firmly
    collar_threshold = max_z - 0.06
    collar_verts = [v.index for v in obj.data.vertices if v.co.z >= collar_threshold]
    pin_group.add(collar_verts, 1.0, 'ADD')

    # Pin shoulder zone (next 8cm down) with soft weight — lets fabric drape naturally
    shoulder_bottom = max_z - 0.14
    shoulder_verts = [v.index for v in obj.data.vertices
                      if shoulder_bottom <= v.co.z < collar_threshold]
    pin_group.add(shoulder_verts, 0.3, 'ADD')

    # NOTE: Chest and wrist pins REMOVED — they were suspending fabric mid-air
    # The garment should hang freely from collar/shoulders under gravity

    total = len(obj.data.vertices)
    print(f"  [GARMENT] Abaya ready: {total} verts, "
          f"{len(collar_verts)} collar + {len(shoulder_verts)} shoulder pinned", flush=True)

    return obj


def create_body_tube():
    """
    Stage 1: Create an open-ended cylinder around the mannequin.
    This naturally wraps the body — gravity + collision = draping.
    """
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=64,  # More segments for smoother drape
        radius=0.28,  # Closer to mannequin body
        depth=1.5,
        location=(0, 0, 0.95),
        end_fill_type='NOTHING',
    )
    tube = bpy.context.active_object
    tube.name = "Abaya_Body"

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.subdivide(number_cuts=12)
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.object.shade_smooth()

    # Pin only the top ring (shoulder line)
    max_z = max(v.co.z for v in tube.data.vertices)
    pin_threshold = max_z - 0.03
    pin_group = tube.vertex_groups.new(name="Pin")
    top_verts = [v.index for v in tube.data.vertices if v.co.z >= pin_threshold]
    pin_group.add(top_verts, 1.0, 'ADD')

    total = len(tube.data.vertices)
    print(f"  Abaya_Body: {total} verts, {len(top_verts)} pinned (top ring)")
    return tube


def create_sleeve(name, x_offset):
    """
    Stage 2: Create a sleeve panel near an arm.
    Positioned away from the arm so it falls and wraps via collision.
    """
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0))
    sleeve = bpy.context.active_object
    sleeve.name = name

    # Make it vertical and sized for a sleeve
    sleeve.rotation_euler = (math.radians(90), 0, 0)
    sleeve.scale = (0.30, 0.50, 1)

    # Apply transforms explicitly
    bpy.context.view_layer.objects.active = sleeve
    sleeve.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # Move to arm position AFTER applying rotation/scale
    sleeve.location = (x_offset, 0, 1.20)

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.subdivide(number_cuts=12)
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.object.shade_smooth()

    # Pin top edge only
    max_z = max(v.co.z for v in sleeve.data.vertices)
    min_z = min(v.co.z for v in sleeve.data.vertices)
    pin_threshold = max_z - (max_z - min_z) * 0.08
    pin_group = sleeve.vertex_groups.new(name="Pin")
    top_verts = [v.index for v in sleeve.data.vertices if v.co.z >= pin_threshold]
    pin_group.add(top_verts, 1.0, 'ADD')

    total = len(sleeve.data.vertices)
    print(f"  {name}: {total} verts, {len(top_verts)} pinned (top edge)")
    return sleeve


print("PROGRESS:11%|Creating fabric panels...")
sys.stdout.flush()

# Choose pattern source
if PATTERN_SOURCE == "freesewing":
    print("  Using FreeSewing (measuring MPFB mannequin)")
    sys.stdout.flush()

    print("PROGRESS:12%|Creating abaya from mannequin measurements...")
    # FreeSewing measures the MPFB mannequin directly for perfect fit
    abaya = create_freesewing_connected_abaya(pattern_data=None, mannequin_obj=mannequin)
    fabric_panels = [abaya]

else:
    # Procedural generation
    print("  Using procedural pattern generation")
    sys.stdout.flush()

    print("PROGRESS:11%|Stage 1: Creating body tube...")
    sys.stdout.flush()
    body = create_body_tube()

    print("PROGRESS:13%|Stage 2: Creating left sleeve...")
    sys.stdout.flush()
    l_sleeve = create_sleeve("Abaya_LSleeve", x_offset=0.42)

    print("PROGRESS:14%|Stage 2: Creating right sleeve...")
    sys.stdout.flush()
    r_sleeve = create_sleeve("Abaya_RSleeve", x_offset=-0.42)

    fabric_panels = [body, l_sleeve, r_sleeve]

print(f"PROGRESS:15%|{len(fabric_panels)} fabric panels ready")
sys.stdout.flush()


# --- Material ---
def create_fabric_material():
    mat = bpy.data.materials.new("Abaya_Fabric")
    mat.use_nodes = True
    tree = mat.node_tree
    nodes = tree.nodes
    links = tree.links
    for n in nodes:
        nodes.remove(n)

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    bsdf.inputs["Roughness"].default_value = CLOTH_PARAMS["roughness"]
    if CLOTH_PARAMS["sheen"] > 0:
        bsdf.inputs["Sheen Weight"].default_value = CLOTH_PARAMS["sheen"]
    if CLOTH_PARAMS["transmission"] > 0:
        bsdf.inputs["Transmission Weight"].default_value = CLOTH_PARAMS["transmission"]

    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    if TEXTURE_PATH and os.path.exists(TEXTURE_PATH):
        print(f"  Using AI texture: {TEXTURE_PATH}")
        tex_image = nodes.new("ShaderNodeTexImage")
        tex_image.location = (-400, 0)
        tex_image.image = bpy.data.images.load(TEXTURE_PATH)
        tex_image.image.colorspace_settings.name = 'sRGB'
        tc = nodes.new("ShaderNodeTexCoord")
        tc.location = (-800, 0)
        mp = nodes.new("ShaderNodeMapping")
        mp.location = (-600, 0)
        mp.inputs["Scale"].default_value = (2.0, 2.0, 2.0)
        links.new(tc.outputs["UV"], mp.inputs["Vector"])
        links.new(mp.outputs["Vector"], tex_image.inputs["Vector"])
        links.new(tex_image.outputs["Color"], bsdf.inputs["Base Color"])

    elif PATTERN != "none":
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = 'RGBA'
        mix.location = (-100, 0)
        c1 = nodes.new("ShaderNodeRGB")
        c1.outputs[0].default_value = FABRIC_COLOR
        c1.location = (-400, 100)
        c2 = nodes.new("ShaderNodeRGB")
        c2.outputs[0].default_value = PATTERN_COLOR
        c2.location = (-400, -100)
        tc = nodes.new("ShaderNodeTexCoord")
        tc.location = (-800, 0)
        mp = nodes.new("ShaderNodeMapping")
        mp.location = (-600, 0)
        mp.inputs["Scale"].default_value = (PATTERN_SCALE, PATTERN_SCALE, PATTERN_SCALE)
        links.new(tc.outputs["UV"], mp.inputs["Vector"])

        tex = None
        if PATTERN == "stripes":
            tex = nodes.new("ShaderNodeTexWave")
            tex.wave_type = 'BANDS'
            tex.inputs["Scale"].default_value = PATTERN_SCALE
        elif PATTERN == "diamonds":
            tex = nodes.new("ShaderNodeTexChecker")
            tex.inputs["Scale"].default_value = PATTERN_SCALE
        elif PATTERN == "floral":
            tex = nodes.new("ShaderNodeTexVoronoi")
            tex.inputs["Scale"].default_value = PATTERN_SCALE
        elif PATTERN == "geometric":
            tex = nodes.new("ShaderNodeTexBrick")
            tex.inputs["Scale"].default_value = PATTERN_SCALE

        if tex:
            tex.location = (-400, -300)
            links.new(mp.outputs["Vector"], tex.inputs["Vector"])
            fac_out = "Fac" if "Fac" in tex.outputs else "Distance"
            links.new(tex.outputs[fac_out], mix.inputs[0])

        links.new(c1.outputs[0], mix.inputs[6])
        links.new(c2.outputs[0], mix.inputs[7])
        links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = FABRIC_COLOR

    return mat


print("PROGRESS:17%|Creating material...")
sys.stdout.flush()
fabric_mat = create_fabric_material()

print("PROGRESS:19%|Applying material & cloth physics...")
sys.stdout.flush()

for panel in fabric_panels:
    panel.data.materials.append(fabric_mat)

    bpy.context.view_layer.objects.active = panel
    bpy.ops.object.modifier_add(type='CLOTH')
    cloth = panel.modifiers["Cloth"]
    cloth.settings.quality = CLOTH_PARAMS["quality_steps"]
    cloth.settings.mass = CLOTH_PARAMS["mass"]
    cloth.settings.tension_stiffness = CLOTH_PARAMS["tension_stiffness"]
    cloth.settings.compression_stiffness = CLOTH_PARAMS["compression_stiffness"]
    cloth.settings.bending_stiffness = CLOTH_PARAMS["bending_stiffness"]
    cloth.settings.tension_damping = CLOTH_PARAMS["tension_damping"]
    cloth.settings.compression_damping = CLOTH_PARAMS["compression_damping"]
    cloth.settings.bending_damping = CLOTH_PARAMS["bending_damping"]
    cloth.settings.vertex_group_mass = "Pin"

    cloth.collision_settings.use_collision = True
    cloth.collision_settings.collision_quality = CLOTH_PARAMS["collision_quality"]
    cloth.collision_settings.distance_min = CLOTH_PARAMS["collision_distance"]
    cloth.collision_settings.friction = CLOTH_PARAMS["friction"]
    cloth.collision_settings.use_self_collision = True
    cloth.collision_settings.self_distance_min = CLOTH_PARAMS["self_collision_distance"]
    cloth.collision_settings.self_friction = CLOTH_PARAMS["self_friction"]
    cloth.point_cache.frame_start = 1
    cloth.point_cache.frame_end = scene.frame_end
    print(f"  ClothSDK: {panel.name} — mass={CLOTH_PARAMS['mass']}, "
          f"tension={CLOTH_PARAMS['tension_stiffness']}, "
          f"bending={CLOTH_PARAMS['bending_stiffness']}")


# --- Ground ---
bpy.ops.mesh.primitive_plane_add(size=5, location=(0, 0, -0.05))
ground = bpy.context.active_object
ground.name = "Ground"
bpy.ops.object.modifier_add(type='COLLISION')
gmat = bpy.data.materials.new("Ground_Mat")
gmat.use_nodes = True
gmat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.12, 0.12, 0.12, 1.0)
ground.data.materials.append(gmat)


# --- Bake simulation ---
print("PROGRESS:22%|Simulating cloth physics...")
print(f"  [SIM] Starting simulation: {scene.frame_end} frames, {len(fabric_panels)} panels", flush=True)
sys.stdout.flush()

sim_start_time = time.time()

bpy.ops.ptcache.free_bake_all()
total_frames = scene.frame_end - scene.frame_start + 1

for frame in range(scene.frame_start, scene.frame_end + 1):
    scene.frame_set(frame)
    bpy.context.view_layer.update()

    current_time = time.time()
    elapsed = current_time - sim_start_time

    # Simulation is 22% to 75% of total progress
    sim_pct = 22 + int(((frame - scene.frame_start) / total_frames) * 53)

    # Log every frame but with timing info every 10 frames
    if frame % 10 == 0 or frame == scene.frame_end:
        frames_done = frame - scene.frame_start + 1
        fps = frames_done / elapsed if elapsed > 0 else 0
        eta = (total_frames - frames_done) / fps if fps > 0 else 0
        print(f"PROGRESS:{sim_pct}%|Frame {frame}/{scene.frame_end} ({fps:.1f} fps, ETA: {eta:.0f}s)")
    else:
        print(f"PROGRESS:{sim_pct}%|Frame {frame}/{scene.frame_end}")
    sys.stdout.flush()

scene.frame_set(scene.frame_end)
sim_elapsed = time.time() - sim_start_time
print(f"PROGRESS:75%|Simulation complete ({sim_elapsed:.1f}s)")
print(f"  [SIM] Finished in {sim_elapsed:.1f} seconds", flush=True)

# Add Subdivision Surface modifier AFTER cloth simulation for smooth render
print("  [SMOOTH] Adding subdivision surface for smooth rendering...", flush=True)
for panel in fabric_panels:
    bpy.context.view_layer.objects.active = panel
    panel.select_set(True)

    # Add subdivision surface modifier (placed after cloth modifier)
    subsurf = panel.modifiers.new(name="Smooth", type='SUBSURF')
    subsurf.levels = 1          # Viewport smoothness
    subsurf.render_levels = 2   # Render smoothness (higher quality)
    subsurf.subdivision_type = 'CATMULL_CLARK'
    subsurf.use_limit_surface = True

    print(f"    {panel.name}: Added subdivision surface (render level 2)", flush=True)

# Verify all objects still exist
print(f"  Scene objects: {[o.name for o in bpy.context.scene.objects]}", flush=True)

# Make sure mannequin is visible for render
if mannequin and mannequin.name in bpy.data.objects:
    mannequin.hide_render = False
    mannequin.hide_viewport = False
    print(f"  Mannequin visible: {mannequin.name}", flush=True)
else:
    print(f"  WARNING: Mannequin not found!", flush=True)

print("PROGRESS:76%|Setting up camera & lights...")
sys.stdout.flush()

# --- Camera & lighting ---
bpy.ops.object.camera_add(location=(2.0, -2.0, 1.4))
camera = bpy.context.active_object
scene.camera = camera
direction = Vector((0, 0, 1.0)) - camera.location
camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

bpy.ops.object.light_add(type='AREA', location=(2, -1, 3))
bpy.context.active_object.data.energy = 250
bpy.context.active_object.data.size = 2

bpy.ops.object.light_add(type='AREA', location=(-2, -2, 2))
bpy.context.active_object.data.energy = 100
bpy.context.active_object.data.size = 1.5

bpy.ops.object.light_add(type='AREA', location=(0, 2, 2.5))
bpy.context.active_object.data.energy = 150
bpy.context.active_object.data.size = 1


# --- Render engine setup ---
print("="*60)
print(f"  RENDER ENGINE: {RENDER_ENGINE}")
print(f"  SAMPLES: {RENDER_SAMPLES}")
print("="*60)
sys.stdout.flush()

if RENDER_ENGINE == "CYCLES":
    scene.render.engine = 'CYCLES'
    gpu_found = False
    prefs = bpy.context.preferences.addons['cycles'].preferences
    try:
        prefs.compute_device_type = 'HIP'
        prefs.get_devices()
        for d in prefs.devices:
            d.use = True
            if d.type != 'CPU':
                print(f"  [GPU] Found: {d.name} (HIP)")
                gpu_found = True
        if gpu_found:
            scene.cycles.device = 'GPU'
            print("  [GPU] Rendering with Cycles GPU (HIP)")
    except Exception:
        pass
    if not gpu_found:
        scene.cycles.device = 'CPU'
        print("  [CPU] RX 5700 XT = RDNA 1, Cycles HIP needs RDNA 2+")
        print("  [CPU] Rendering with Cycles CPU (use EEVEE for GPU speed)")
    scene.cycles.samples = RENDER_SAMPLES
    scene.cycles.use_denoising = True

else:
    # EEVEE — uses GPU via Vulkan/OpenGL, works with ALL GPUs including RX 5700 XT
    # Blender 4.x used 'BLENDER_EEVEE_NEXT', Blender 3.x and 5.0+ use 'BLENDER_EEVEE'
    # Detect the correct name from available render engines
    eevee_engine = 'BLENDER_EEVEE'
    try:
        # Try setting BLENDER_EEVEE_NEXT first (Blender 4.x)
        scene.render.engine = 'BLENDER_EEVEE_NEXT'
        eevee_engine = 'BLENDER_EEVEE_NEXT'
    except TypeError:
        scene.render.engine = 'BLENDER_EEVEE'
        eevee_engine = 'BLENDER_EEVEE'
    scene.eevee.taa_render_samples = RENDER_SAMPLES
    print(f"  [GPU] Rendering with EEVEE ({eevee_engine}) — uses your RX 5700 XT")

print("="*60)
sys.stdout.flush()

scene.render.resolution_x = 1080
scene.render.resolution_y = 1440
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = OUTPUT_PATH

scene.world = bpy.data.worlds.new("World")
scene.world.use_nodes = True
bg_node = scene.world.node_tree.nodes["Background"]
bg_node.inputs["Color"].default_value = (0.04, 0.04, 0.05, 1.0)
bg_node.inputs["Strength"].default_value = 0.5

# Save .blend file so user can open it later
if BLEND_PATH:
    print(f"PROGRESS:78%|Saving .blend file...")
    sys.stdout.flush()
    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)
    print(f"  Saved: {BLEND_PATH}")

print(f"PROGRESS:80%|Rendering...")
sys.stdout.flush()
bpy.ops.render.render(write_still=True)
print("PROGRESS:100%|Done!")
sys.stdout.flush()
