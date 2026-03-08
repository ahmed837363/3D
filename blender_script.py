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
    # Sewing approach: wider panels need more time to pull together + settle
    # Phase 1 (frames 1-60): sewing springs pull panels together
    # Phase 2 (frames 60-100): gravity drapes fabric on body
    # Phase 3 (frames 100-140): settling into final position
    base_frames = 140
    extra_frames = int(fabric_mass * 50)
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

# Add collision modifier — HIGH FRICTION is critical with minimal pinning
# The fabric must grip the mannequin's shoulders via collision, not pins
bpy.ops.object.modifier_add(type='COLLISION')
mannequin.collision.thickness_outer = 0.002
mannequin.collision.thickness_inner = 0.002
mannequin.collision.cloth_friction = 80.0  # High friction — fabric grips shoulders

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


def create_flat_panel_from_outline(name, outline_2d, subdivisions=2):
    """
    Create a FLAT filled mesh panel from a 2D FreeSewing outline.

    Takes outline vertices in cm (x, y) where y is height,
    creates a filled polygon in Blender's XZ plane (Y=0),
    and subdivides for cloth simulation.

    Returns the Blender mesh object.
    """
    # Convert outline from cm to meters, map to XZ plane (Y=0)
    # FreeSewing: (x_cm, y_cm) → Blender: (x_m, 0, -y_m)
    # y in pattern = distance from top, so negate for Z (height grows up)
    outline_3d = [(x / 100.0, 0.0, -y / 100.0) for x, y in outline_2d]

    if len(outline_3d) < 3:
        print(f"    [PANEL] ERROR: Not enough vertices for {name}", flush=True)
        return None

    bm = bmesh.new()

    # Create outline vertices
    bm_verts = []
    for co in outline_3d:
        v = bm.verts.new(co)
        bm_verts.append(v)
    bm.verts.ensure_lookup_table()

    # Create edges forming the outline loop
    for i in range(len(bm_verts)):
        bm.edges.new([bm_verts[i], bm_verts[(i + 1) % len(bm_verts)]])
    bm.edges.ensure_lookup_table()

    # Fill the outline to create a face
    # Use edgeloop fill for the single closed boundary
    try:
        result = bmesh.ops.triangle_fill(bm, use_beauty=True,
                                         use_dissolve=False,
                                         edges=bm.edges[:])
        if not result['geom']:
            # Fallback: try contextual fill
            bmesh.ops.contextual_create(bm, geom=bm.verts[:] + bm.edges[:])
    except Exception as e:
        print(f"    [PANEL] triangle_fill failed for {name}: {e}, trying contextual_create", flush=True)
        try:
            bmesh.ops.contextual_create(bm, geom=bm.verts[:] + bm.edges[:])
        except Exception as e2:
            print(f"    [PANEL] contextual_create also failed: {e2}", flush=True)

    # Subdivide for cloth simulation density
    if subdivisions > 0 and len(bm.faces) > 0:
        bmesh.ops.subdivide_edges(bm,
                                  edges=bm.edges[:],
                                  cuts=subdivisions,
                                  use_grid_fill=True)

    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    # Create Blender object
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    # UV unwrap
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.object.shade_smooth()
    print(f"    [PANEL] {name}: {len(obj.data.vertices)} verts, "
          f"{len(obj.data.polygons)} faces", flush=True)
    return obj


def create_sewing_edges(obj, panel_bounds, seam_pairs):
    """
    Create sewing edges between panels by bridging matching boundary edges
    and then deleting the bridge faces, leaving only naked sewing edges.

    Blender's cloth sewing engine uses these naked edges as invisible threads
    to pull flat panels together — exactly like real tailoring.

    Args:
        obj: The joined mesh object containing all panels
        panel_bounds: Dict mapping panel name → (boundary_verts_indices, y_offset)
        seam_pairs: List of (panel_a_name, panel_b_name, seam_type) tuples
    """
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    # Find boundary edges (edges with only 1 face)
    boundary_edges = [e for e in bm.edges if len(e.link_faces) <= 1]
    print(f"    [SEW] Found {len(boundary_edges)} boundary edges", flush=True)

    sewing_edges_created = 0

    for panel_a, panel_b, seam_type in seam_pairs:
        bounds_a = panel_bounds.get(panel_a)
        bounds_b = panel_bounds.get(panel_b)
        if not bounds_a or not bounds_b:
            continue

        a_verts_set, a_y = bounds_a
        b_verts_set, b_y = bounds_b

        # Get boundary edges belonging to each panel
        a_boundary = []
        b_boundary = []
        for e in boundary_edges:
            v0_idx = e.verts[0].index
            v1_idx = e.verts[1].index
            if v0_idx in a_verts_set and v1_idx in a_verts_set:
                a_boundary.append(e)
            elif v0_idx in b_verts_set and v1_idx in b_verts_set:
                b_boundary.append(e)

        if seam_type == "side":
            # Side seam: match right edge of front to right edge of back
            # (or left edge to left edge)
            # Side edges are the ones furthest from center (max |x|)
            a_side = [e for e in a_boundary
                      if all(abs(e.verts[i].co.x) > 0.05 for i in range(2))]
            b_side = [e for e in b_boundary
                      if all(abs(e.verts[i].co.x) > 0.05 for i in range(2))]

            # Match by Z height proximity
            for ea in a_side:
                ea_z = (ea.verts[0].co.z + ea.verts[1].co.z) / 2
                ea_x_sign = 1 if (ea.verts[0].co.x + ea.verts[1].co.x) > 0 else -1
                best_eb = None
                best_dist = float('inf')
                for eb in b_side:
                    eb_x_sign = 1 if (eb.verts[0].co.x + eb.verts[1].co.x) > 0 else -1
                    if ea_x_sign != eb_x_sign:
                        continue  # Must be same side (left-left or right-right)
                    eb_z = (eb.verts[0].co.z + eb.verts[1].co.z) / 2
                    dist = abs(ea_z - eb_z)
                    if dist < best_dist and dist < 0.05:  # 5cm tolerance
                        best_dist = dist
                        best_eb = eb

                if best_eb is not None:
                    # Create sewing edge: connect midpoints with a naked edge
                    mid_a = (ea.verts[0].co + ea.verts[1].co) / 2
                    mid_b = (best_eb.verts[0].co + best_eb.verts[1].co) / 2
                    # Connect closest vertex pairs
                    for va in ea.verts:
                        closest_vb = min(best_eb.verts, key=lambda vb: (va.co - vb.co).length)
                        if (va.co - closest_vb.co).length < 0.5:  # Max 50cm
                            try:
                                bm.edges.new([va, closest_vb])
                                sewing_edges_created += 1
                            except ValueError:
                                pass  # Edge already exists

        elif seam_type == "armhole":
            # Armhole: match sleeve cap edges to bodice armhole edges
            # Sleeve boundary edges are those near the shoulder height
            for ea in a_boundary:
                ea_z = (ea.verts[0].co.z + ea.verts[1].co.z) / 2
                best_eb = None
                best_dist = float('inf')
                for eb in b_boundary:
                    eb_z = (eb.verts[0].co.z + eb.verts[1].co.z) / 2
                    # Match by Z height and X proximity
                    dist = math.sqrt((ea_z - eb_z)**2)
                    ea_x = (ea.verts[0].co.x + ea.verts[1].co.x) / 2
                    eb_x = (eb.verts[0].co.x + eb.verts[1].co.x) / 2
                    x_dist = abs(ea_x - eb_x)
                    if dist < 0.05 and x_dist < 0.3:
                        total_dist = dist + x_dist * 0.5
                        if total_dist < best_dist:
                            best_dist = total_dist
                            best_eb = eb

                if best_eb is not None:
                    for va in ea.verts:
                        closest_vb = min(best_eb.verts,
                                         key=lambda vb: (va.co - vb.co).length)
                        if (va.co - closest_vb.co).length < 0.4:
                            try:
                                bm.edges.new([va, closest_vb])
                                sewing_edges_created += 1
                            except ValueError:
                                pass

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    print(f"    [SEW] Created {sewing_edges_created} sewing edges", flush=True)
    return sewing_edges_created


def create_freesewing_connected_abaya(pattern_data, mannequin_obj=None) -> object:
    """
    Create abaya using FLAT PANEL SEWING approach.

    Professional garment simulation — mimics real tailoring:
    1. Measure mannequin body at key landmarks
    2. Draft FreeSewing pattern pieces (mathematically accurate 2D panels)
    3. Create FLAT mesh panels from the pattern outlines
    4. Position panels around the mannequin (front, back, sleeves)
    5. Create sewing edges between matching panel boundaries
    6. Blender's cloth sewing engine pulls panels together like real stitching
    7. Gravity + collision = natural draping with seam-driven folds

    This replaces the old procedural tube approach that ignored the FreeSewing
    pattern data entirely.
    """
    if mannequin_obj is None:
        raise ValueError("Mannequin required for automatic measurements")

    print(f"  [FreeSewing] Auto-measuring mannequin...", flush=True)

    # Step 1: Get body measurements
    body = measure_mannequin_body(mannequin_obj)
    arm_data = measure_mannequin_arms(mannequin_obj, body)

    # Step 2: FreeSewing pattern generation
    if not FREESEWING_AVAILABLE:
        raise RuntimeError("FreeSewing pattern system required for sewing approach")

    fs_measurements = BodyMeasurements.from_mannequin_body(body, arm_data)

    # =========================================================================
    # ABAYA EASE OVERRIDE — Force loose, flowing silhouette
    #
    # Real abayas have 25-35cm of extra width at chest/waist/hips.
    # The default FreeSewing ease (16/20/18cm) is for fitted garments.
    # We override with proper abaya ease so the panels are wide enough
    # to billow and create elegant vertical folds when gravity pulls them.
    # =========================================================================
    fs_measurements.chestEase = 30.0    # +30cm chest (was 16)
    fs_measurements.waistEase = 35.0    # +35cm waist (was 20) — abayas are very loose here
    fs_measurements.hipsEase = 30.0     # +30cm hips (was 18)
    fs_measurements.sleeveEase = 18.0   # +18cm sleeves (was 12) — flowing sleeves
    print(f"    [EASE] Abaya ease applied: chest+30, waist+35, hips+30, sleeve+18", flush=True)

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

    # Export SVG for visualization
    try:
        job_id = os.path.basename(OUTPUT_PATH).split('.')[0]
        svg_dir = os.path.join(os.path.dirname(OUTPUT_PATH), f"{job_id}_patterns")
        os.makedirs(svg_dir, exist_ok=True)
        exporter = FreeSewingSVGExporter(pieces)
        svg_path = exporter.export_combined(os.path.join(svg_dir, "abaya_pattern.svg"))
        print(f"    SVG exported: {svg_path}", flush=True)
    except Exception as e:
        print(f"    SVG export failed (non-critical): {e}", flush=True)

    # Step 3: Body reference measurements for panel positioning
    shoulder_z = body["shoulder_z"]
    bust_r = body["bust_r"]
    hip_r = body["hip_r"]

    print(f"    Height: {body['height']:.2f}m", flush=True)
    print(f"    Torso radii: bust={bust_r:.3f}, hip={hip_r:.3f}", flush=True)
    print(f"    Arms: upper={arm_data['upper_arm_radius']:.3f}, "
          f"elbow={arm_data['elbow_radius']:.3f}, "
          f"wrist={arm_data['wrist_radius']:.3f}", flush=True)

    # =========================================================================
    # Step 4: Create FLAT panels from FreeSewing pattern outlines
    #
    # Each pattern piece becomes a filled, subdivided flat mesh.
    # Panels are positioned around the mannequin but NOT wrapped —
    # Blender's sewing engine will pull them together.
    # =========================================================================
    print(f"    Creating flat panels from FreeSewing patterns...", flush=True)

    # Subdivision level: 3 gives good cloth density without excessive verts
    subdiv = 3

    panel_objects = {}
    panel_vert_ranges = {}  # Track vertex index ranges after joining

    # Gap between panels and body — panels start flat near the body surface
    body_gap = bust_r + 0.03  # Small gap outside body radius

    for piece_name, piece in pieces.items():
        outline = piece.vertices
        if not outline or len(outline) < 3:
            print(f"    [WARN] Skipping {piece_name}: not enough vertices", flush=True)
            continue

        panel = create_flat_panel_from_outline(piece_name, outline, subdivisions=subdiv)
        if panel is None:
            continue

        # Position panel around the mannequin
        # Panels are in XZ plane (Y=0). Move them to their positions.
        bounds = piece.get_bounds()
        panel_height_cm = bounds[3] - bounds[1]  # Height in cm
        panel_top_z = shoulder_z + 0.05  # Start slightly above shoulders

        if piece_name == "Front_Bodice":
            # Front panel: offset in -Y (front of mannequin)
            panel.location = (0, -body_gap, panel_top_z)
            panel_objects[piece_name] = panel

        elif piece_name == "Back_Bodice":
            # Back panel: offset in +Y (back of mannequin), flip to face inward
            panel.location = (0, body_gap, panel_top_z)
            panel.rotation_euler = (0, 0, math.pi)  # Rotate 180° so inner face faces body
            bpy.context.view_layer.objects.active = panel
            panel.select_set(True)
            bpy.ops.object.transform_apply(rotation=True)
            panel_objects[piece_name] = panel

        elif piece_name == "Left_Sleeve":
            # Left sleeve: rotate 90° and position at left arm
            arm_x = arm_data["left_arm_start"]
            arm_z = arm_data["left_arm_z"]
            panel.rotation_euler = (0, 0, math.radians(90))
            bpy.context.view_layer.objects.active = panel
            panel.select_set(True)
            bpy.ops.object.transform_apply(rotation=True)
            panel.location = (arm_x + 0.05, 0, arm_z + 0.05)
            panel_objects[piece_name] = panel

        elif piece_name == "Right_Sleeve":
            # Right sleeve: rotate -90° and position at right arm
            arm_x = arm_data["right_arm_start"]
            arm_z = arm_data["right_arm_z"]
            panel.rotation_euler = (0, 0, math.radians(-90))
            bpy.context.view_layer.objects.active = panel
            panel.select_set(True)
            bpy.ops.object.transform_apply(rotation=True)
            panel.location = (arm_x - 0.05, 0, arm_z + 0.05)
            panel_objects[piece_name] = panel

    if not panel_objects:
        raise RuntimeError("No panels created from FreeSewing patterns")

    print(f"    Created {len(panel_objects)} flat panels", flush=True)

    # =========================================================================
    # Step 5: Join all panels into a single mesh
    # =========================================================================
    print(f"    Joining panels...", flush=True)

    # Track vertex ranges for each panel (needed for sewing edge creation)
    vert_offset = 0
    panel_bounds = {}

    bpy.ops.object.select_all(action='DESELECT')
    first_panel = None
    for piece_name, panel in panel_objects.items():
        panel.select_set(True)
        if first_panel is None:
            first_panel = panel

        n_verts = len(panel.data.vertices)
        vert_indices = set(range(vert_offset, vert_offset + n_verts))
        y_offset = panel.location.y
        panel_bounds[piece_name] = (vert_indices, y_offset)
        vert_offset += n_verts

    bpy.context.view_layer.objects.active = first_panel
    bpy.ops.object.join()

    obj = bpy.context.active_object
    obj.name = "Abaya_Sewn"

    print(f"    Joined mesh: {len(obj.data.vertices)} verts, "
          f"{len(obj.data.polygons)} faces", flush=True)

    # =========================================================================
    # Step 6: Create sewing edges between panels
    #
    # Sewing edges are naked edges (no faces) connecting boundary vertices
    # of adjacent panels. Blender's cloth sewing engine treats these as
    # invisible threads that pull the panels together.
    # =========================================================================
    print(f"    Creating sewing edges...", flush=True)

    seam_pairs = [
        # Side seams: front ↔ back
        ("Front_Bodice", "Back_Bodice", "side"),
        # Armhole seams: sleeves ↔ bodice
        ("Front_Bodice", "Left_Sleeve", "armhole"),
        ("Front_Bodice", "Right_Sleeve", "armhole"),
        ("Back_Bodice", "Left_Sleeve", "armhole"),
        ("Back_Bodice", "Right_Sleeve", "armhole"),
    ]

    sew_count = create_sewing_edges(obj, panel_bounds, seam_pairs)

    # UV unwrap the joined mesh
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.shade_smooth()

    # =========================================================================
    # Step 7: MINIMAL pinning — gravity + mannequin collision does the work
    #
    # Real abayas hang from the shoulders purely by gravity pressing fabric
    # against the body. Over-pinning creates a "wire hanger" effect where
    # the top of the garment is frozen rigid in mid-air.
    #
    # We pin ONLY a handful of vertices at the very back of the neckline —
    # just enough to prevent the garment from sliding off entirely.
    # The MPFB mannequin's collision modifier (with high friction) handles
    # keeping the fabric draped on the shoulders naturally.
    # =========================================================================
    max_z = max(v.co.z for v in obj.data.vertices)
    pin_group = obj.vertex_groups.new(name="Pin")

    # Pin ONLY the very top neckline vertices (top 1.5cm)
    collar_threshold = max_z - 0.015
    collar_verts = [v.index for v in obj.data.vertices if v.co.z >= collar_threshold]

    # Narrow to back-of-neck only (Y > 0) if too many
    if len(collar_verts) > 20:
        collar_verts = [v.index for v in obj.data.vertices
                        if v.co.z >= collar_threshold and v.co.y > 0]

    # Cap at 8 verts max — take only the very highest
    if len(collar_verts) > 8:
        sorted_by_z = sorted(collar_verts,
                             key=lambda i: obj.data.vertices[i].co.z,
                             reverse=True)
        collar_verts = sorted_by_z[:8]

    pin_group.add(collar_verts, 1.0, 'ADD')

    total = len(obj.data.vertices)
    print(f"  [SEWING] Abaya ready: {total} verts, "
          f"{len(collar_verts)} neck-pin verts (minimal), "
          f"{sew_count} sewing edges", flush=True)

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

    # Enable sewing: naked edges between panels act as invisible threads
    # that pull the flat panels together — mimics real garment stitching
    if PATTERN_SOURCE == "freesewing":
        sewing_enabled = False
        # Blender API changed across versions:
        # Blender 2.8-3.x: use_sewing_springs
        # Blender 4.x+: use_sewing or sewing_force_max
        for sew_attr in ['use_sewing_springs', 'use_sewing']:
            if hasattr(cloth.settings, sew_attr):
                setattr(cloth.settings, sew_attr, True)
                sewing_enabled = True
                print(f"  [SEW] Sewing enabled via {sew_attr}")
                break

        if hasattr(cloth.settings, 'sewing_force_max'):
            cloth.settings.sewing_force_max = 20.0  # Stronger pull for wider abaya panels
            print(f"  [SEW] Sewing force_max=20.0")
        elif hasattr(cloth.settings, 'shrink_min'):
            # Fallback: use shrink to pull fabric closer
            cloth.settings.shrink_min = -0.3
            print(f"  [SEW] Using shrink_min=-0.3 as sewing fallback")

        if not sewing_enabled:
            # If no sewing attribute exists, use shrink as alternative
            if hasattr(cloth.settings, 'shrink_min'):
                cloth.settings.shrink_min = -0.3
                print(f"  [SEW] No sewing API found, using shrink_min=-0.3")
            else:
                print(f"  [SEW] WARNING: No sewing support in this Blender version")

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
