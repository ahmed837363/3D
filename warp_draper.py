"""
Warp XPBD Cloth Draper — Standalone headless cloth simulation

Uses NVIDIA Warp's CPU backend to simulate XPBD cloth physics.
This replaces Blender's native cloth modifier which crashes with
self-collision + sewing edges.

Pipeline:
  1. Load mannequin collision mesh (OBJ)
  2. Load flat garment panels (JSON with 2D outlines from FreeSewing)
  3. Triangulate & position panels around mannequin
  4. Run XPBD simulation: sewing → draping → settling
  5. Export draped garment mesh (OBJ)

Called by blender_script.py via:
    python warp_draper.py <json_params_file>

Inputs (JSON):
  - mannequin_obj: path to mannequin collision mesh
  - panels: dict of panel_name → {outline: [(x,y),...], position: [x,y,z], ...}
  - fabric: {mass, stretch_compliance, bend_compliance, friction, ...}
  - seam_pairs: [(panel_a, panel_b, type), ...]
  - output_obj: path to write draped mesh
  - sim_config: {steps, substeps, dt, sewing_steps, ...}

Output: draped garment OBJ file
"""

import sys
import os
import json
import math
import time
import numpy as np

# Warp import with graceful fallback
try:
    import warp as wp
    WARP_AVAILABLE = True
except ImportError:
    WARP_AVAILABLE = False

try:
    import trimesh
    TRIMESH_AVAILABLE = True
except ImportError:
    TRIMESH_AVAILABLE = False


# =============================================================================
# XPBD Warp Kernels — GPU/CPU parallel constraint solvers
# =============================================================================

if WARP_AVAILABLE:
    wp.init()

    @wp.kernel
    def integrate_particles(
        positions: wp.array(dtype=wp.vec3),
        velocities: wp.array(dtype=wp.vec3),
        inv_masses: wp.array(dtype=float),
        predicted: wp.array(dtype=wp.vec3),
        gravity: wp.vec3,
        dt: float,
        damping: float,
    ):
        """Semi-implicit Euler integration with gravity and damping."""
        i = wp.tid()
        if inv_masses[i] > 0.0:  # Not pinned
            vel = velocities[i] * (1.0 - damping) + gravity * dt
            predicted[i] = positions[i] + vel * dt
        else:
            predicted[i] = positions[i]

    @wp.kernel
    def solve_stretch_constraints(
        predicted: wp.array(dtype=wp.vec3),
        inv_masses: wp.array(dtype=float),
        edge_indices: wp.array(dtype=wp.vec2i),
        rest_lengths: wp.array(dtype=float),
        compliance: float,
        dt: float,
    ):
        """XPBD distance constraint — maintains edge lengths."""
        i = wp.tid()
        idx = edge_indices[i]
        p0 = predicted[idx[0]]
        p1 = predicted[idx[1]]
        w0 = inv_masses[idx[0]]
        w1 = inv_masses[idx[1]]
        w_sum = w0 + w1
        if w_sum < 1.0e-8:
            return

        diff = p1 - p0
        dist = wp.length(diff)
        if dist < 1.0e-8:
            return

        rest = rest_lengths[i]
        alpha = compliance / (dt * dt)
        C = dist - rest
        delta_lambda = -C / (w_sum + alpha)
        correction = wp.normalize(diff) * delta_lambda

        if w0 > 0.0:
            wp.atomic_sub(predicted, idx[0], correction * w0)
        if w1 > 0.0:
            wp.atomic_add(predicted, idx[1], correction * w1)

    @wp.kernel
    def solve_bend_constraints(
        predicted: wp.array(dtype=wp.vec3),
        inv_masses: wp.array(dtype=float),
        bend_indices: wp.array(dtype=wp.vec4i),
        rest_angles: wp.array(dtype=float),
        compliance: float,
        dt: float,
    ):
        """XPBD dihedral angle constraint — controls bending stiffness."""
        i = wp.tid()
        idx = bend_indices[i]
        p0 = predicted[idx[0]]
        p1 = predicted[idx[1]]
        p2 = predicted[idx[2]]
        p3 = predicted[idx[3]]

        # Shared edge: p0-p1, opposite vertices: p2, p3
        e = p1 - p0
        e_len = wp.length(e)
        if e_len < 1.0e-8:
            return

        n = wp.normalize(e)

        # Project opposite vertices onto plane perpendicular to edge
        d2 = p2 - p0
        d3 = p3 - p0

        # Vectors from edge to opposite vertices (perpendicular component)
        t2 = d2 - n * wp.dot(d2, n)
        t3 = d3 - n * wp.dot(d3, n)

        len2 = wp.length(t2)
        len3 = wp.length(t3)
        if len2 < 1.0e-8 or len3 < 1.0e-8:
            return

        # Dihedral angle
        cos_angle = wp.dot(t2, t3) / (len2 * len3)
        cos_angle = wp.clamp(cos_angle, -1.0, 1.0)

        angle = wp.acos(cos_angle)
        rest = rest_angles[i]

        C = angle - rest
        if wp.abs(C) < 1.0e-6:
            return

        # Simple correction: push p2 and p3 apart or together
        alpha = compliance / (dt * dt)
        w2 = inv_masses[idx[2]]
        w3 = inv_masses[idx[3]]
        w_sum = w2 + w3
        if w_sum < 1.0e-8:
            return

        delta_lambda = -C / (w_sum + alpha)

        # Apply correction along the perpendicular directions
        if w2 > 0.0:
            corr2 = wp.normalize(t2) * (delta_lambda * w2 * 0.5)
            wp.atomic_add(predicted, idx[2], corr2)
        if w3 > 0.0:
            corr3 = wp.normalize(t3) * (-delta_lambda * w3 * 0.5)
            wp.atomic_add(predicted, idx[3], corr3)

    @wp.kernel
    def solve_body_collision(
        predicted: wp.array(dtype=wp.vec3),
        inv_masses: wp.array(dtype=float),
        body_mesh_id: wp.uint64,
        collision_margin: float,
    ):
        """Resolve collisions against the mannequin body mesh."""
        i = wp.tid()
        if inv_masses[i] <= 0.0:
            return

        p = predicted[i]

        # Query closest point on body mesh
        face_idx = int(0)
        face_u = float(0.0)
        face_v = float(0.0)
        sign = float(0.0)

        found = wp.mesh_query_point(body_mesh_id, p, collision_margin * 4.0,
                                     sign, face_idx, face_u, face_v)
        if not found:
            return

        closest = wp.mesh_eval_position(body_mesh_id, face_idx, face_u, face_v)
        diff = p - closest
        dist = wp.length(diff)

        if dist < collision_margin and dist > 1.0e-8:
            # Push particle outside the collision margin
            normal = wp.normalize(diff)
            predicted[i] = closest + normal * collision_margin

    @wp.kernel
    def solve_sewing_constraints(
        predicted: wp.array(dtype=wp.vec3),
        inv_masses: wp.array(dtype=float),
        sew_indices: wp.array(dtype=wp.vec2i),
        target_dist: float,
        stiffness: float,
        dt: float,
    ):
        """Sewing springs — pull boundary vertices of adjacent panels together."""
        i = wp.tid()
        idx = sew_indices[i]
        p0 = predicted[idx[0]]
        p1 = predicted[idx[1]]
        w0 = inv_masses[idx[0]]
        w1 = inv_masses[idx[1]]
        w_sum = w0 + w1
        if w_sum < 1.0e-8:
            return

        diff = p1 - p0
        dist = wp.length(diff)
        if dist < 1.0e-8:
            return

        C = dist - target_dist
        if C < 0.0:
            return  # Already close enough

        compliance = 1.0 / (stiffness + 1.0e-8)
        alpha = compliance / (dt * dt)
        delta_lambda = -C / (w_sum + alpha)
        correction = wp.normalize(diff) * delta_lambda

        if w0 > 0.0:
            wp.atomic_sub(predicted, idx[0], correction * w0)
        if w1 > 0.0:
            wp.atomic_add(predicted, idx[1], correction * w1)

    @wp.kernel
    def update_velocities(
        positions: wp.array(dtype=wp.vec3),
        predicted: wp.array(dtype=wp.vec3),
        velocities: wp.array(dtype=wp.vec3),
        inv_masses: wp.array(dtype=float),
        dt: float,
    ):
        """Compute new velocities from position changes."""
        i = wp.tid()
        if inv_masses[i] > 0.0:
            velocities[i] = (predicted[i] - positions[i]) / dt
        positions[i] = predicted[i]


# =============================================================================
# Mesh Utilities
# =============================================================================

def triangulate_outline(outline_2d, subdivisions=3):
    """
    Triangulate a 2D outline polygon and subdivide for cloth simulation.

    Args:
        outline_2d: List of (x, y) points in cm
        subdivisions: Number of subdivision passes

    Returns:
        vertices: np.array shape (N, 3) in meters (x, 0, -y)
        triangles: np.array shape (M, 3) face indices
    """
    from scipy.spatial import Delaunay

    # Convert cm to meters, map to XZ plane
    points_2d = np.array(outline_2d, dtype=np.float64)
    points_2d_m = points_2d / 100.0  # cm → meters

    # Create boundary-constrained triangulation
    # First: create a dense point cloud inside the outline
    min_xy = points_2d_m.min(axis=0)
    max_xy = points_2d_m.max(axis=0)
    width = max_xy[0] - min_xy[0]
    height = max_xy[1] - min_xy[1]

    # Grid resolution based on subdivision level
    grid_res = int(8 * (2 ** subdivisions))
    grid_res = min(grid_res, 64)  # Cap to prevent too many vertices

    # Generate grid points inside the outline
    from matplotlib.path import Path
    outline_path = Path(points_2d_m)

    xs = np.linspace(min_xy[0], max_xy[0], grid_res)
    ys = np.linspace(min_xy[1], max_xy[1], grid_res)
    grid_x, grid_y = np.meshgrid(xs, ys)
    grid_points = np.column_stack([grid_x.ravel(), grid_y.ravel()])

    # Filter to points inside the outline
    inside = outline_path.contains_points(grid_points)
    interior_points = grid_points[inside]

    # Combine boundary + interior points
    all_points_2d = np.vstack([points_2d_m, interior_points])

    # Triangulate
    tri = Delaunay(all_points_2d)
    triangles = tri.simplices

    # Filter triangles: keep only those whose centroid is inside the outline
    centroids = all_points_2d[triangles].mean(axis=1)
    mask = outline_path.contains_points(centroids)
    triangles = triangles[mask]

    # Convert to 3D: (x, 0, -y) — y in pattern becomes -z in Blender space
    vertices_3d = np.zeros((len(all_points_2d), 3), dtype=np.float64)
    vertices_3d[:, 0] = all_points_2d[:, 0]     # X
    vertices_3d[:, 1] = 0.0                       # Y = 0 (flat)
    vertices_3d[:, 2] = -all_points_2d[:, 1]     # Z = -y (height grows up)

    return vertices_3d, triangles


def triangulate_outline_simple(outline_2d, target_edge_length=0.03):
    """
    Simple triangulation using trimesh for reliability.
    Falls back if scipy/matplotlib aren't available.
    """
    points_2d = np.array(outline_2d, dtype=np.float64) / 100.0  # cm → m

    # Create a 2D polygon path and extrude minimally, then take a cross-section
    # Actually, use trimesh's 2D triangulation
    try:
        from trimesh.creation import triangulate_polygon
        from shapely.geometry import Polygon

        poly = Polygon(points_2d)
        vertices_2d, faces = triangulate_polygon(poly, engine='earcut')

        vertices_3d = np.zeros((len(vertices_2d), 3), dtype=np.float64)
        vertices_3d[:, 0] = vertices_2d[:, 0]
        vertices_3d[:, 2] = -vertices_2d[:, 1]
        return vertices_3d, faces
    except Exception:
        pass

    # Fallback: fan triangulation from centroid
    n = len(points_2d)
    centroid = points_2d.mean(axis=0)
    all_pts = np.vstack([points_2d, [centroid]])
    center_idx = len(all_pts) - 1

    triangles = []
    for i in range(n):
        j = (i + 1) % n
        triangles.append([i, j, center_idx])
    triangles = np.array(triangles)

    vertices_3d = np.zeros((len(all_pts), 3), dtype=np.float64)
    vertices_3d[:, 0] = all_pts[:, 0]
    vertices_3d[:, 2] = -all_pts[:, 1]
    return vertices_3d, triangles


def build_edges(triangles):
    """Extract unique edges from triangle faces."""
    edges = set()
    for tri in triangles:
        for k in range(3):
            a = int(tri[k])
            b = int(tri[(k + 1) % 3])
            edge = (min(a, b), max(a, b))
            edges.add(edge)
    return np.array(sorted(edges), dtype=np.int32)


def build_bend_pairs(triangles, edges):
    """
    Find adjacent triangle pairs sharing an edge.
    Returns array of [v0, v1, v2, v3] where v0-v1 is shared edge,
    v2 is opposite vertex in tri A, v3 is opposite in tri B.
    """
    # Build edge → triangle map
    edge_to_tris = {}
    for ti, tri in enumerate(triangles):
        for k in range(3):
            a = int(tri[k])
            b = int(tri[(k + 1) % 3])
            edge = (min(a, b), max(a, b))
            if edge not in edge_to_tris:
                edge_to_tris[edge] = []
            edge_to_tris[edge].append((ti, int(tri[(k + 2) % 3])))

    bend_pairs = []
    for edge, tris in edge_to_tris.items():
        if len(tris) == 2:
            v0, v1 = edge
            v2 = tris[0][1]  # Opposite vertex in tri A
            v3 = tris[1][1]  # Opposite vertex in tri B
            bend_pairs.append([v0, v1, v2, v3])

    return np.array(bend_pairs, dtype=np.int32) if bend_pairs else np.zeros((0, 4), dtype=np.int32)


def find_boundary_vertices(triangles, num_verts):
    """Find vertices on the boundary (connected to edges with only 1 face)."""
    edge_count = {}
    for tri in triangles:
        for k in range(3):
            a = int(tri[k])
            b = int(tri[(k + 1) % 3])
            edge = (min(a, b), max(a, b))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    boundary_verts = set()
    for (a, b), count in edge_count.items():
        if count == 1:
            boundary_verts.add(a)
            boundary_verts.add(b)
    return boundary_verts


def match_sewing_vertices(verts_a, boundary_a, verts_b, boundary_b,
                          max_dist=0.5, seam_type="side"):
    """
    Match boundary vertices between two panels for sewing.
    Returns list of (idx_a, idx_b) pairs.
    """
    pairs = []
    max_z_a = None
    max_z_b = None
    if seam_type == "shoulder":
        if boundary_a:
            max_z_a = max(verts_a[i][2] for i in boundary_a)
        if boundary_b:
            max_z_b = max(verts_b[i][2] for i in boundary_b)

    for ia in boundary_a:
        pa = verts_a[ia]
        if seam_type == "shoulder":
            if max_z_a is None or pa[2] <= max_z_a - 0.15 or abs(pa[0]) <= 0.08:
                continue

        best_ib = None
        best_dist = max_dist
        for ib in boundary_b:
            pb = verts_b[ib]
            # Match by Z height (vertical position)
            z_dist = abs(pa[2] - pb[2])
            if seam_type == "side":
                # Side seam: match same-side X edges
                x_sign_a = 1 if pa[0] > 0 else -1
                x_sign_b = 1 if pb[0] > 0 else -1
                if x_sign_a != x_sign_b:
                    continue
                # Match by Z proximity
                if z_dist < best_dist:
                    best_dist = z_dist
                    best_ib = ib
            elif seam_type == "armhole":
                total_dist = z_dist + abs(pa[0] - pb[0]) * 0.5
                if total_dist < best_dist:
                    best_dist = total_dist
                    best_ib = ib
            elif seam_type == "shoulder":
                if max_z_b is None or pb[2] <= max_z_b - 0.15 or abs(pb[0]) <= 0.08:
                    continue
                x_dist = abs(pa[0] - pb[0])
                if x_dist < best_dist and x_dist < 0.1:
                    best_dist = x_dist
                    best_ib = ib

        if best_ib is not None:
            pairs.append((ia, best_ib))

    return pairs


def write_obj(filepath, vertices, triangles, uvs=None):
    """Write a simple OBJ file."""
    with open(filepath, 'w') as f:
        f.write("# Warp XPBD Draped Garment\n")
        for v in vertices:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        if uvs is not None:
            for uv in uvs:
                f.write(f"vt {uv[0]:.6f} {uv[1]:.6f}\n")
        for tri in triangles:
            if uvs is not None:
                f.write(f"f {tri[0]+1}/{tri[0]+1} {tri[1]+1}/{tri[1]+1} {tri[2]+1}/{tri[2]+1}\n")
            else:
                f.write(f"f {tri[0]+1} {tri[1]+1} {tri[2]+1}\n")


# =============================================================================
# XPBD Cloth Simulator
# =============================================================================

class WarpClothSimulator:
    """
    XPBD cloth simulator using NVIDIA Warp.

    Simulates garment draping on a mannequin body:
    1. Sewing phase: pull flat panels together via distance constraints
    2. Draping phase: gravity + body collision settles the garment
    3. Settling phase: damping reduces residual motion
    """

    def __init__(self, device="cpu"):
        self.device = device
        self.vertices = None
        self.triangles = None
        self.edges = None
        self.bend_pairs = None
        self.pin_indices = None
        self.sewing_pairs = None

        # Simulation arrays (Warp)
        self.wp_positions = None
        self.wp_velocities = None
        self.wp_predicted = None
        self.wp_inv_masses = None
        self.wp_edge_indices = None
        self.wp_rest_lengths = None
        self.wp_bend_indices = None
        self.wp_rest_angles = None
        self.wp_sew_indices = None
        self.body_mesh = None
        self.body_mesh_id = None

    def setup_garment(self, vertices, triangles, pin_indices=None,
                      mass_per_area=0.15):
        """
        Initialize garment mesh for simulation.

        Args:
            vertices: (N, 3) float array
            triangles: (M, 3) int array
            pin_indices: set of vertex indices to pin (zero inv_mass)
            mass_per_area: kg/m² surface density
        """
        self.vertices = np.array(vertices, dtype=np.float32)
        self.triangles = np.array(triangles, dtype=np.int32)
        self.pin_indices = pin_indices or set()

        n_verts = len(self.vertices)

        # Build edges and bend pairs
        self.edges = build_edges(self.triangles)
        self.bend_pairs = build_bend_pairs(self.triangles, self.edges)

        # Compute per-vertex mass from triangle areas
        masses = np.zeros(n_verts, dtype=np.float32)
        for tri in self.triangles:
            v0, v1, v2 = self.vertices[tri]
            area = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0))
            mass = area * mass_per_area / 3.0
            for vi in tri:
                masses[vi] += mass

        # Inverse masses (pinned vertices get 0)
        inv_masses = np.zeros(n_verts, dtype=np.float32)
        for i in range(n_verts):
            if i in self.pin_indices or masses[i] < 1.0e-10:
                inv_masses[i] = 0.0
            else:
                inv_masses[i] = 1.0 / masses[i]

        # Compute rest lengths
        rest_lengths = np.zeros(len(self.edges), dtype=np.float32)
        for i, (a, b) in enumerate(self.edges):
            rest_lengths[i] = np.linalg.norm(self.vertices[a] - self.vertices[b])

        # Compute rest dihedral angles
        rest_angles = np.zeros(len(self.bend_pairs), dtype=np.float32)
        for i, (v0, v1, v2, v3) in enumerate(self.bend_pairs):
            p0, p1, p2, p3 = [self.vertices[j] for j in [v0, v1, v2, v3]]
            e = p1 - p0
            e_len = np.linalg.norm(e)
            if e_len < 1e-8:
                rest_angles[i] = math.pi
                continue
            n_hat = e / e_len
            d2 = p2 - p0
            d3 = p3 - p0
            t2 = d2 - n_hat * np.dot(d2, n_hat)
            t3 = d3 - n_hat * np.dot(d3, n_hat)
            l2 = np.linalg.norm(t2)
            l3 = np.linalg.norm(t3)
            if l2 < 1e-8 or l3 < 1e-8:
                rest_angles[i] = math.pi
                continue
            cos_a = np.clip(np.dot(t2, t3) / (l2 * l3), -1.0, 1.0)
            rest_angles[i] = math.acos(cos_a)

        # Initialize Warp arrays
        self.wp_positions = wp.array(self.vertices, dtype=wp.vec3, device=self.device)
        self.wp_velocities = wp.zeros(n_verts, dtype=wp.vec3, device=self.device)
        self.wp_predicted = wp.array(self.vertices, dtype=wp.vec3, device=self.device)
        self.wp_inv_masses = wp.array(inv_masses, dtype=float, device=self.device)

        self.wp_edge_indices = wp.array(self.edges, dtype=wp.vec2i, device=self.device)
        self.wp_rest_lengths = wp.array(rest_lengths, dtype=float, device=self.device)

        if len(self.bend_pairs) > 0:
            self.wp_bend_indices = wp.array(self.bend_pairs, dtype=wp.vec4i, device=self.device)
            self.wp_rest_angles = wp.array(rest_angles, dtype=float, device=self.device)

        print(f"  [WARP] Garment: {n_verts} verts, {len(self.triangles)} tris, "
              f"{len(self.edges)} edges, {len(self.bend_pairs)} bend pairs, "
              f"{len(self.pin_indices)} pinned", flush=True)

    def setup_body_collision(self, body_vertices, body_triangles):
        """Load mannequin body as collision mesh."""
        body_v = np.array(body_vertices, dtype=np.float32)
        body_t = np.array(body_triangles, dtype=np.int32)

        wp_body_v = wp.array(body_v, dtype=wp.vec3, device=self.device)
        wp_body_t = wp.array(body_t, dtype=wp.int32, device=self.device)

        self.body_mesh = wp.Mesh(
            points=wp_body_v,
            indices=wp_body_t.flatten(),
        )
        self.body_mesh_id = self.body_mesh.id

        print(f"  [WARP] Body collision: {len(body_v)} verts, "
              f"{len(body_t)} tris", flush=True)

    def setup_sewing(self, sewing_pairs):
        """Set up sewing constraints between panels."""
        if not sewing_pairs:
            self.wp_sew_indices = None
            return

        sew_array = np.array(sewing_pairs, dtype=np.int32)
        self.wp_sew_indices = wp.array(sew_array, dtype=wp.vec2i, device=self.device)

        print(f"  [WARP] Sewing: {len(sewing_pairs)} vertex pairs", flush=True)

    def simulate(self, steps=200, substeps=8, dt=1.0/60.0,
                 stretch_compliance=0.0001, bend_compliance=0.1,
                 collision_margin=0.003, damping=0.01,
                 sewing_steps=80, sewing_stiffness=10.0,
                 progress_callback=None):
        """
        Run XPBD simulation.

        Args:
            steps: Total simulation steps
            substeps: XPBD solver iterations per step
            dt: Time step
            stretch_compliance: Inverse of stretch stiffness (lower = stiffer)
            bend_compliance: Inverse of bending stiffness (lower = stiffer)
            collision_margin: Collision distance from body surface
            damping: Velocity damping per step (0-1)
            sewing_steps: Steps during which sewing constraints are active
            sewing_stiffness: Sewing spring stiffness
            progress_callback: fn(step, total_steps, message)
        """
        n_verts = len(self.vertices)
        gravity = wp.vec3(0.0, 0.0, -9.81)
        sub_dt = dt / float(substeps)

        print(f"  [WARP] Simulating: {steps} steps × {substeps} substeps, "
              f"dt={dt:.4f}", flush=True)
        print(f"  [WARP] Stretch compliance={stretch_compliance:.6f}, "
              f"Bend compliance={bend_compliance:.4f}", flush=True)

        sim_start = time.time()

        for step in range(steps):
            # Sewing: decrease target distance over time
            if step < sewing_steps and self.wp_sew_indices is not None:
                sew_progress = float(step) / float(sewing_steps)
                # Ease-in-out: start slow, speed up, slow down
                sew_target = (1.0 - sew_progress) * 0.3  # 30cm → 0cm
                sew_target = max(sew_target, 0.001)  # Minimum 1mm
            else:
                sew_target = 0.001

            for sub in range(substeps):
                # 1. Integrate: predict positions
                wp.launch(integrate_particles, dim=n_verts,
                          inputs=[self.wp_positions, self.wp_velocities,
                                  self.wp_inv_masses, self.wp_predicted,
                                  gravity, sub_dt, damping],
                          device=self.device)

                # 2. Solve stretch constraints
                if len(self.edges) > 0:
                    wp.launch(solve_stretch_constraints, dim=len(self.edges),
                              inputs=[self.wp_predicted, self.wp_inv_masses,
                                      self.wp_edge_indices, self.wp_rest_lengths,
                                      stretch_compliance, sub_dt],
                              device=self.device)

                # 3. Solve bend constraints
                if self.wp_bend_indices is not None and len(self.bend_pairs) > 0:
                    wp.launch(solve_bend_constraints, dim=len(self.bend_pairs),
                              inputs=[self.wp_predicted, self.wp_inv_masses,
                                      self.wp_bend_indices, self.wp_rest_angles,
                                      bend_compliance, sub_dt],
                              device=self.device)

                # 4. Solve sewing constraints (during sewing phase)
                if step < sewing_steps and self.wp_sew_indices is not None:
                    n_sew = self.wp_sew_indices.shape[0]
                    wp.launch(solve_sewing_constraints, dim=n_sew,
                              inputs=[self.wp_predicted, self.wp_inv_masses,
                                      self.wp_sew_indices, sew_target,
                                      sewing_stiffness, sub_dt],
                              device=self.device)

                # 5. Body collision
                if self.body_mesh_id is not None:
                    wp.launch(solve_body_collision, dim=n_verts,
                              inputs=[self.wp_predicted, self.wp_inv_masses,
                                      self.body_mesh_id, collision_margin],
                              device=self.device)

                # 6. Update velocities and positions
                wp.launch(update_velocities, dim=n_verts,
                          inputs=[self.wp_positions, self.wp_predicted,
                                  self.wp_velocities, self.wp_inv_masses,
                                  sub_dt],
                          device=self.device)

            # Progress reporting
            if step % 20 == 0 or step == steps - 1:
                elapsed = time.time() - sim_start
                pct = int((step + 1) / steps * 100)
                phase = "SEWING" if step < sewing_steps else "DRAPING"
                fps = (step + 1) / elapsed if elapsed > 0 else 0
                eta = (steps - step - 1) / fps if fps > 0 else 0
                msg = f"[{phase}] Step {step+1}/{steps} ({fps:.1f} sps, ETA: {eta:.0f}s)"
                print(f"PROGRESS:{pct}%|{msg}", flush=True)
                if progress_callback:
                    progress_callback(step, steps, msg)

            sys.stdout.flush()

        # Sync results back to numpy
        wp.synchronize()
        final_positions = self.wp_positions.numpy()

        sim_time = time.time() - sim_start
        print(f"  [WARP] Simulation complete: {sim_time:.1f}s "
              f"({steps * substeps} total iterations)", flush=True)

        return final_positions

    def get_result_mesh(self):
        """Get final draped mesh as (vertices, triangles)."""
        final_verts = self.wp_positions.numpy()
        return final_verts, self.triangles


# =============================================================================
# Main Entry Point
# =============================================================================

def run_draper(params):
    """
    Main draping function.

    Args:
        params: dict with keys:
            mannequin_obj, panels, fabric, seam_pairs,
            output_obj, sim_config
    """
    if not WARP_AVAILABLE:
        print("ERROR: warp-lang not installed. pip install warp-lang", flush=True)
        sys.exit(1)

    print("  [WARP] Starting XPBD cloth draper...", flush=True)

    # --- Load mannequin collision mesh ---
    mannequin_path = params["mannequin_obj"]
    print(f"  [WARP] Loading mannequin: {mannequin_path}", flush=True)

    body_mesh = trimesh.load(mannequin_path, process=False)
    body_verts = np.array(body_mesh.vertices, dtype=np.float32)
    body_tris = np.array(body_mesh.faces, dtype=np.int32)
    print(f"  [WARP] Mannequin: {len(body_verts)} verts, {len(body_tris)} tris", flush=True)

    # --- Build garment panels ---
    panels = params["panels"]
    fabric = params["fabric"]
    sim_config = params.get("sim_config", {})

    all_vertices = []
    all_triangles = []
    all_sewing_pairs = []
    pin_indices = set()
    panel_info = {}  # Track vertex ranges per panel
    vert_offset = 0

    for panel_name, panel_data in panels.items():
        outline = panel_data["outline"]
        position = np.array(panel_data["position"], dtype=np.float32)

        print(f"  [WARP] Creating panel: {panel_name} ({len(outline)} outline verts)", flush=True)

        # Triangulate the panel
        try:
            verts, tris = triangulate_outline(outline, subdivisions=2)
        except Exception as e:
            print(f"  [WARP] Scipy triangulation failed: {e}, using simple", flush=True)
            verts, tris = triangulate_outline_simple(outline)

        # Apply rotation if specified
        rotation = panel_data.get("rotation", [0, 0, 0])
        if any(abs(r) > 0.001 for r in rotation):
            # Simple Z-axis rotation for panel positioning
            rz = rotation[2]
            cos_r = math.cos(rz)
            sin_r = math.sin(rz)
            rotated = np.zeros_like(verts)
            rotated[:, 0] = verts[:, 0] * cos_r - verts[:, 2] * sin_r
            rotated[:, 1] = verts[:, 1]
            rotated[:, 2] = verts[:, 0] * sin_r + verts[:, 2] * cos_r
            verts = rotated

        # Apply position offset
        verts += position

        # Track panel vertex range
        panel_info[panel_name] = {
            "vert_start": vert_offset,
            "vert_end": vert_offset + len(verts),
            "boundary": find_boundary_vertices(tris, len(verts)),
        }

        # Pin top vertices (collar area)
        max_z = verts[:, 2].max()
        collar_threshold = max_z - 0.015  # Top 1.5cm
        for i, v in enumerate(verts):
            if v[2] >= collar_threshold:
                pin_indices.add(vert_offset + i)

        # Offset triangle indices
        tris_offset = tris + vert_offset
        all_vertices.append(verts)
        all_triangles.append(tris_offset)
        vert_offset += len(verts)

        print(f"    {panel_name}: {len(verts)} verts, {len(tris)} tris", flush=True)

    # Combine all panels
    garment_verts = np.vstack(all_vertices).astype(np.float32)
    garment_tris = np.vstack(all_triangles).astype(np.int32)

    # --- Build sewing constraints ---
    seam_pairs = params.get("seam_pairs", [])
    for panel_a, panel_b, seam_type in seam_pairs:
        if panel_a not in panel_info or panel_b not in panel_info:
            continue

        info_a = panel_info[panel_a]
        info_b = panel_info[panel_b]

        # Get boundary vertices (with global offset)
        boundary_a = {v + info_a["vert_start"] for v in info_a["boundary"]}
        boundary_b = {v + info_b["vert_start"] for v in info_b["boundary"]}

        pairs = match_sewing_vertices(
            garment_verts, boundary_a,
            garment_verts, boundary_b,
            max_dist=0.5, seam_type=seam_type
        )
        all_sewing_pairs.extend(pairs)

    print(f"  [WARP] Total garment: {len(garment_verts)} verts, "
          f"{len(garment_tris)} tris, {len(all_sewing_pairs)} sewing pairs, "
          f"{len(pin_indices)} pinned", flush=True)

    # --- Initialize simulator ---
    sim = WarpClothSimulator(device="cpu")
    sim.setup_garment(
        garment_verts, garment_tris,
        pin_indices=pin_indices,
        mass_per_area=fabric.get("mass", 0.15),
    )
    sim.setup_body_collision(body_verts, body_tris)
    sim.setup_sewing(all_sewing_pairs)

    # --- Run simulation ---
    steps = sim_config.get("steps", 200)
    substeps = sim_config.get("substeps", 8)
    dt = sim_config.get("dt", 1.0 / 60.0)
    sewing_steps = sim_config.get("sewing_steps", 80)

    stretch_compliance = fabric.get("stretch_compliance", 0.0004)
    bend_compliance = fabric.get("bend_compliance", 1.0)
    collision_margin = fabric.get("collision_margin", 0.003)
    damping = fabric.get("damping", 0.02)
    sewing_stiffness = fabric.get("sewing_stiffness", 10.0)

    final_positions = sim.simulate(
        steps=steps,
        substeps=substeps,
        dt=dt,
        stretch_compliance=stretch_compliance,
        bend_compliance=bend_compliance,
        collision_margin=collision_margin,
        damping=damping,
        sewing_steps=sewing_steps,
        sewing_stiffness=sewing_stiffness,
    )

    # --- Generate UVs (simple planar projection) ---
    uvs = np.zeros((len(final_positions), 2), dtype=np.float32)
    x_range = final_positions[:, 0].max() - final_positions[:, 0].min()
    z_range = final_positions[:, 2].max() - final_positions[:, 2].min()
    if x_range > 1e-6:
        uvs[:, 0] = (final_positions[:, 0] - final_positions[:, 0].min()) / x_range
    if z_range > 1e-6:
        uvs[:, 1] = (final_positions[:, 2] - final_positions[:, 2].min()) / z_range

    # --- Write output OBJ ---
    output_path = params["output_obj"]
    write_obj(output_path, final_positions, garment_tris, uvs=uvs)
    print(f"  [WARP] Saved draped mesh: {output_path}", flush=True)
    print(f"  [WARP] Result: {len(final_positions)} verts, {len(garment_tris)} tris", flush=True)

    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python warp_draper.py <params.json>")
        sys.exit(1)

    params_file = sys.argv[1]
    with open(params_file, 'r') as f:
        params = json.load(f)

    result = run_draper(params)
    print(f"WARP_RESULT:{result}", flush=True)

