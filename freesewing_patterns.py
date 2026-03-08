"""
FreeSewing-Inspired Pattern Generator for Abaya Garments

Based on FreeSewing's parametric design philosophy:
- Measurements drive all pattern calculations
- Smooth Bezier curves for natural garment shaping
- Modular pattern pieces with clear seam allowances
- Clean, predictable mesh generation for cloth simulation

This module generates patterns optimized for Blender cloth physics with:
- Higher vertex density along curves for smoother draping
- Consistent winding order for proper normals
- Seam-aligned vertex placement for realistic fold lines

Reference: https://freesewing.org/docs/about/concepts
"""

import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
from xml.etree import ElementTree as ET
import os


# =============================================================================
# MEASUREMENTS - FreeSewing-style body measurements
# =============================================================================

@dataclass
class BodyMeasurements:
    """
    Body measurements in centimeters, following FreeSewing conventions.
    
    FreeSewing uses a standardized set of measurements that map to
    specific body landmarks. This ensures patterns scale correctly
    across different body types.
    """
    # Vertical measurements
    height: float = 165.0
    hpsToWaistBack: float = 42.0       # High point shoulder to waist (back)
    hpsToWaistFront: float = 44.0      # High point shoulder to waist (front)
    waistToHips: float = 12.0          # Natural waist to hip line
    waistToFloor: float = 105.0        # Waist to floor
    waistToKnee: float = 58.0          # Waist to knee
    waistToUpperLeg: float = 24.0      # Waist to upper thigh
    
    # Circumference measurements
    chest: float = 92.0                # Full chest circumference
    waist: float = 74.0                # Natural waist
    hips: float = 98.0                 # Hip circumference
    neck: float = 38.0                 # Neck circumference
    
    # Width measurements
    shoulderToShoulder: float = 42.0   # Across back shoulder to shoulder
    shoulderSlope: float = 45.0        # Shoulder angle (degrees)
    
    # Arm measurements
    shoulderToWrist: float = 60.0      # Shoulder point to wrist
    shoulderToElbow: float = 35.0      # Shoulder to elbow
    biceps: float = 30.0               # Upper arm circumference
    wrist: float = 16.0                # Wrist circumference
    
    # Ease values (for loose abaya fit)
    chestEase: float = 16.0            # Extra room at chest
    waistEase: float = 20.0            # Extra room at waist
    hipsEase: float = 18.0             # Extra room at hips
    sleeveEase: float = 12.0           # Extra room in sleeves
    
    @classmethod
    def from_size(cls, size: str) -> "BodyMeasurements":
        """
        Create measurements from standard size code.
        Based on international sizing standards with proportional scaling.
        """
        # Base measurements for size M
        base = cls()
        
        # Size scaling factors (relative to M)
        scale_factors = {
            "XS": {"circ": 0.89, "length": 0.97},
            "S":  {"circ": 0.95, "length": 0.98},
            "M":  {"circ": 1.00, "length": 1.00},
            "L":  {"circ": 1.09, "length": 1.01},
            "XL": {"circ": 1.20, "length": 1.02},
            "XXL": {"circ": 1.30, "length": 1.03},
        }
        
        factors = scale_factors.get(size.upper(), scale_factors["M"])
        circ_scale = factors["circ"]
        len_scale = factors["length"]
        
        return cls(
            # Scale height-related measurements
            height=base.height * len_scale,
            hpsToWaistBack=base.hpsToWaistBack * len_scale,
            hpsToWaistFront=base.hpsToWaistFront * len_scale,
            waistToHips=base.waistToHips,
            waistToFloor=base.waistToFloor * len_scale,
            waistToKnee=base.waistToKnee * len_scale,
            waistToUpperLeg=base.waistToUpperLeg,
            # Scale circumference measurements
            chest=base.chest * circ_scale,
            waist=base.waist * circ_scale,
            hips=base.hips * circ_scale,
            neck=base.neck * circ_scale,
            # Scale width measurements
            shoulderToShoulder=base.shoulderToShoulder * circ_scale,
            shoulderSlope=base.shoulderSlope,
            # Scale arm measurements
            shoulderToWrist=base.shoulderToWrist * len_scale,
            shoulderToElbow=base.shoulderToElbow * len_scale,
            biceps=base.biceps * circ_scale,
            wrist=base.wrist * circ_scale,
            # Keep ease proportional
            chestEase=base.chestEase * circ_scale,
            waistEase=base.waistEase * circ_scale,
            hipsEase=base.hipsEase * circ_scale,
            sleeveEase=base.sleeveEase * circ_scale,
        )

    @classmethod
    def from_custom(cls, measurements: Dict[str, float]) -> "BodyMeasurements":
        """Create from custom measurement dictionary (for future UI integration)."""
        base = cls()
        for key, value in measurements.items():
            if hasattr(base, key):
                setattr(base, key, value)
        return base

    @classmethod
    def from_mannequin_body(cls, body: Dict[str, float], arm_data: Dict[str, float]) -> "BodyMeasurements":
        """
        Create measurements from MPFB mannequin auto-measured body data.

        Converts radii (meters) → circumferences (cm) using 2*pi*r.
        Heights are converted from Z positions to relative distances (cm).

        Args:
            body: Dict from measure_mannequin_body() with keys like
                  neck_r, shoulder_r, bust_r, waist_r, hip_r, etc. (meters)
                  and neck_z, shoulder_z, waist_z, etc. (Z positions)
            arm_data: Dict from measure_mannequin_arms() with arm dimensions
        """
        import math as _m

        # Convert radius (m) → circumference (cm): C = 2*pi*r * 100
        def r_to_circ(r): return 2 * _m.pi * r * 100

        # Convert Z distances to cm
        def z_to_cm(z1, z2): return abs(z1 - z2) * 100

        height_m = body.get("height", 1.69)
        height_cm = height_m * 100

        # Vertical measurements from Z positions
        shoulder_z = body.get("shoulder_z", 0)
        waist_z = body.get("waist_z", 0)
        hip_z = body.get("hip_z", 0)
        knee_z = body.get("knee_z", 0)
        min_z = body.get("min_z", 0)
        upper_thigh_z = body.get("upper_thigh_z", 0)

        hps_to_waist = z_to_cm(shoulder_z, waist_z)
        waist_to_hips = z_to_cm(waist_z, hip_z)
        waist_to_floor = z_to_cm(waist_z, min_z)
        waist_to_knee = z_to_cm(waist_z, knee_z)
        waist_to_upper_leg = z_to_cm(waist_z, upper_thigh_z)

        # Circumferences from radii
        chest_circ = r_to_circ(body.get("bust_r", 0.18))
        waist_circ = r_to_circ(body.get("waist_r", 0.17))
        hip_circ = r_to_circ(body.get("hip_r", 0.18))
        neck_circ = r_to_circ(body.get("neck_r", 0.06))

        # Shoulder width: distance between arm starts
        left_start = arm_data.get("left_arm_start", 0.27)
        right_start = arm_data.get("right_arm_start", -0.27)
        shoulder_to_shoulder = abs(left_start - right_start) * 100  # m → cm

        # Arm measurements
        arm_length_m = arm_data.get("left_arm_length", 0.23)
        arm_length_cm = arm_length_m * 100
        # Scale to realistic proportions (MPFB T-pose arms are compressed)
        shoulder_to_wrist = max(arm_length_cm * 2.5, 55.0)
        shoulder_to_elbow = shoulder_to_wrist * 0.58

        biceps_circ = r_to_circ(arm_data.get("upper_arm_radius", 0.05))
        wrist_circ = r_to_circ(arm_data.get("wrist_radius", 0.03))

        return cls(
            height=height_cm,
            hpsToWaistBack=hps_to_waist,
            hpsToWaistFront=hps_to_waist + 2.0,  # Front is slightly longer
            waistToHips=waist_to_hips,
            waistToFloor=waist_to_floor,
            waistToKnee=waist_to_knee,
            waistToUpperLeg=waist_to_upper_leg,
            chest=chest_circ,
            waist=waist_circ,
            hips=hip_circ,
            neck=neck_circ,
            shoulderToShoulder=shoulder_to_shoulder,
            shoulderSlope=45.0,
            shoulderToWrist=shoulder_to_wrist,
            shoulderToElbow=shoulder_to_elbow,
            biceps=biceps_circ,
            wrist=wrist_circ,
            # Ease for loose abaya fit
            chestEase=16.0,
            waistEase=20.0,
            hipsEase=18.0,
            sleeveEase=12.0,
        )


# =============================================================================
# GEOMETRY UTILITIES - Bezier curves and path operations
# =============================================================================

def cubic_bezier(p0: Tuple[float, float], p1: Tuple[float, float],
                 p2: Tuple[float, float], p3: Tuple[float, float],
                 steps: int = 20) -> List[Tuple[float, float]]:
    """
    Evaluate cubic Bezier curve with adaptive step count.
    
    FreeSewing uses cubic Bezier curves extensively for smooth pattern
    lines. This implementation generates enough points for smooth
    cloth simulation without excessive geometry.
    
    Args:
        p0: Start point
        p1: First control point
        p2: Second control point
        p3: End point
        steps: Number of segments (more = smoother)
    
    Returns:
        List of (x, y) points along the curve
    """
    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        
        # Cubic Bezier formula
        x = (u**3 * p0[0] + 
             3 * u**2 * t * p1[0] + 
             3 * u * t**2 * p2[0] + 
             t**3 * p3[0])
        y = (u**3 * p0[1] + 
             3 * u**2 * t * p1[1] + 
             3 * u * t**2 * p2[1] + 
             t**3 * p3[1])
        
        points.append((x, y))
    
    return points


def quadratic_bezier(p0: Tuple[float, float], p1: Tuple[float, float],
                     p2: Tuple[float, float], steps: int = 16) -> List[Tuple[float, float]]:
    """Evaluate quadratic Bezier curve."""
    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        
        x = u**2 * p0[0] + 2 * u * t * p1[0] + t**2 * p2[0]
        y = u**2 * p0[1] + 2 * u * t * p1[1] + t**2 * p2[1]
        
        points.append((x, y))
    
    return points


def smooth_curve_through_points(points: List[Tuple[float, float]], 
                                 tension: float = 0.5) -> List[Tuple[float, float]]:
    """
    Generate smooth curve through control points using Catmull-Rom spline.
    Converts to Bezier segments for consistent output.
    """
    if len(points) < 2:
        return points
    
    result = [points[0]]
    
    for i in range(len(points) - 1):
        p0 = points[max(0, i - 1)]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[min(len(points) - 1, i + 2)]
        
        # Calculate control points from Catmull-Rom to Bezier conversion
        d1 = ((p2[0] - p0[0]) * tension / 3, (p2[1] - p0[1]) * tension / 3)
        d2 = ((p3[0] - p1[0]) * tension / 3, (p3[1] - p1[1]) * tension / 3)
        
        cp1 = (p1[0] + d1[0], p1[1] + d1[1])
        cp2 = (p2[0] - d2[0], p2[1] - d2[1])
        
        # Generate Bezier segment
        segment = cubic_bezier(p1, cp1, cp2, p2, steps=8)
        result.extend(segment[1:])  # Skip first point (duplicate)
    
    return result


def mirror_points_x(points: List[Tuple[float, float]], 
                    include_center: bool = True) -> List[Tuple[float, float]]:
    """
    Mirror points across the Y axis (x=0) to create symmetric patterns.
    
    Args:
        points: Points on right side (positive X or center)
        include_center: If True, don't duplicate points at x=0
    
    Returns:
        Full symmetric point list (left + right)
    """
    # Separate center points (x ≈ 0) from side points
    tolerance = 0.001
    center_points = [(x, y) for x, y in points if abs(x) < tolerance]
    right_points = [(x, y) for x, y in points if x >= tolerance]
    
    # Create mirrored left side (reversed order for correct winding)
    left_points = [(-x, y) for x, y in reversed(right_points)]
    
    # Combine: left → center → right (or appropriate order based on context)
    if include_center:
        return left_points + center_points + right_points
    else:
        return left_points + right_points


# =============================================================================
# PATTERN PIECES - FreeSewing-style modular components
# =============================================================================

@dataclass
class PatternPiece:
    """
    A single pattern piece with outline, notches, and metadata.
    
    FreeSewing patterns are composed of discrete pieces that are
    sewn together. Each piece has:
    - Outline path (the cutting line)
    - Seam allowance (offset from cutting line)
    - Grainline (fabric orientation)
    - Notches (alignment marks)
    """
    name: str
    vertices: List[Tuple[float, float]] = field(default_factory=list)
    seam_allowance: float = 1.5  # cm
    grainline_start: Optional[Tuple[float, float]] = None
    grainline_end: Optional[Tuple[float, float]] = None
    notches: List[Tuple[float, float]] = field(default_factory=list)
    
    def get_bounds(self) -> Tuple[float, float, float, float]:
        """Return (min_x, min_y, max_x, max_y)."""
        if not self.vertices:
            return (0, 0, 0, 0)
        xs = [p[0] for p in self.vertices]
        ys = [p[1] for p in self.vertices]
        return (min(xs), min(ys), max(xs), max(ys))
    
    def to_blender_vertices(self) -> List[List[float]]:
        """
        Convert to Blender-ready vertex format.
        
        Format: [[x, 0, -y], ...] where Y becomes Z (height)
        Units converted from cm to meters.
        """
        return [[x / 100.0, 0.0, -y / 100.0] for x, y in self.vertices]


class AbayaDrafter:
    """
    FreeSewing-inspired abaya pattern drafter.
    
    Creates mathematically accurate pattern pieces using:
    - Parametric calculations from body measurements
    - Smooth Bezier curves for natural shaping
    - Proper ease distribution for garment comfort
    - Consistent vertex density for cloth simulation
    """
    
    def __init__(self, measurements: BodyMeasurements):
        self.m = measurements
        self.pieces: Dict[str, PatternPiece] = {}
        
    def draft_all(self) -> Dict[str, PatternPiece]:
        """Draft complete abaya pattern."""
        self._draft_front_panel()
        self._draft_back_panel()
        self._draft_sleeves()
        return self.pieces
    
    def _draft_front_panel(self):
        """
        Draft front panel: neckline to hem, full width.
        
        Uses FreeSewing's approach of:
        1. Establishing key points from measurements
        2. Connecting with appropriate curves
        3. Mirroring for symmetry
        """
        m = self.m
        
        # Calculated values (FreeSewing-style parametric approach)
        quarter_chest = (m.chest + m.chestEase) / 4
        quarter_waist = (m.waist + m.waistEase) / 4
        quarter_hips = (m.hips + m.hipsEase) / 4
        
        # Neckline parameters
        neck_width = m.neck / 6 + 0.7
        front_neck_drop = 7.0  # Deeper scoop for abaya
        
        # Shoulder parameters
        shoulder_length = m.shoulderToShoulder / 2 - neck_width
        shoulder_slope_rad = math.radians(m.shoulderSlope)
        shoulder_drop = shoulder_length * math.sin(shoulder_slope_rad) * 0.18
        shoulder_x = neck_width + shoulder_length
        shoulder_y = shoulder_drop
        
        # Armhole parameters
        armhole_depth = m.chest / 5 + 7
        underarm_x = quarter_chest
        underarm_y = armhole_depth
        
        # Panel length
        total_length = m.hpsToWaistFront + m.waistToFloor
        
        # Hem width (A-line flare for abaya silhouette)
        hem_width = quarter_hips + 18
        
        # === Build right half of panel (center to side) ===
        half_points = []
        
        # 1. Center front neckline (lowest point of scoop)
        half_points.append((0.0, front_neck_drop))
        
        # 2. Neckline curve (center → shoulder)
        neck_curve = cubic_bezier(
            (0.0, front_neck_drop),                    # Start: center neck
            (neck_width * 0.5, front_neck_drop * 0.3), # Control 1
            (neck_width * 0.85, 1.5),                  # Control 2
            (shoulder_x, shoulder_y),                  # End: shoulder point
            steps=14
        )
        half_points.extend(neck_curve[1:])
        
        # 3. Armhole curve (shoulder → underarm)
        # FreeSewing uses smooth curves that follow natural body contours
        armhole_curve = cubic_bezier(
            (shoulder_x, shoulder_y),                              # Start: shoulder
            (shoulder_x + 1.5, shoulder_y + armhole_depth * 0.4),  # Control 1
            (underarm_x + 0.5, armhole_depth * 0.5),               # Control 2
            (underarm_x, underarm_y),                              # End: underarm
            steps=16
        )
        half_points.extend(armhole_curve[1:])
        
        # 4. Side seam: underarm → waist → hip → hem
        waist_y = m.hpsToWaistFront
        hip_y = waist_y + m.waistToHips
        
        # Smooth side seam with subtle shaping
        side_seam = smooth_curve_through_points([
            (underarm_x, underarm_y),
            (quarter_waist + 1, waist_y * 0.7),      # Slight taper above waist
            (quarter_waist, waist_y),                 # Natural waist
            (quarter_hips, hip_y),                    # Hip line
            (quarter_hips + 5, hip_y + 30),           # Begin A-line flare
            (hem_width, total_length),                # Hem edge
        ], tension=0.4)
        half_points.extend(side_seam[1:])
        
        # 5. Center hem
        half_points.append((0.0, total_length))
        
        # === Mirror for full panel ===
        # Build full panel: left side (mirrored) + right side
        right_side = half_points[:-1]  # Exclude center hem (will be added once)
        left_side = [(-x, y) for x, y in reversed(right_side[1:])]  # Skip center neck
        
        full_vertices = left_side + right_side + [(0.0, total_length)]
        
        # Close the path back to start
        # The vertices should form a closed loop
        
        self.pieces["Front_Bodice"] = PatternPiece(
            name="Front_Bodice",
            vertices=full_vertices,
            grainline_start=(0, armhole_depth),
            grainline_end=(0, total_length - 20),
        )
    
    def _draft_back_panel(self):
        """
        Draft back panel: shallower neckline, similar body.
        """
        m = self.m
        
        quarter_chest = (m.chest + m.chestEase) / 4
        quarter_waist = (m.waist + m.waistEase) / 4
        quarter_hips = (m.hips + m.hipsEase) / 4
        
        # Back neckline is shallower
        neck_width = m.neck / 6 + 1.0
        back_neck_drop = 2.5
        
        shoulder_length = m.shoulderToShoulder / 2 - neck_width
        shoulder_slope_rad = math.radians(m.shoulderSlope)
        shoulder_drop = shoulder_length * math.sin(shoulder_slope_rad) * 0.18
        shoulder_x = neck_width + shoulder_length + 1.0  # Slightly longer for ease
        shoulder_y = shoulder_drop
        
        armhole_depth = m.chest / 5 + 7
        underarm_x = quarter_chest
        underarm_y = armhole_depth
        
        total_length = m.hpsToWaistBack + m.waistToFloor
        hem_width = quarter_hips + 18
        
        # === Build right half ===
        half_points = []
        
        # 1. Center back neck
        half_points.append((0.0, back_neck_drop))
        
        # 2. Back neckline curve (flatter than front)
        neck_curve = cubic_bezier(
            (0.0, back_neck_drop),
            (neck_width * 0.4, back_neck_drop * 0.2),
            (neck_width * 0.8, 0.5),
            (shoulder_x, shoulder_y),
            steps=12
        )
        half_points.extend(neck_curve[1:])
        
        # 3. Armhole curve
        armhole_curve = cubic_bezier(
            (shoulder_x, shoulder_y),
            (shoulder_x + 2.0, shoulder_y + armhole_depth * 0.45),
            (underarm_x + 0.8, armhole_depth * 0.5),
            (underarm_x, underarm_y),
            steps=16
        )
        half_points.extend(armhole_curve[1:])
        
        # 4. Side seam
        waist_y = m.hpsToWaistBack
        hip_y = waist_y + m.waistToHips
        
        side_seam = smooth_curve_through_points([
            (underarm_x, underarm_y),
            (quarter_waist + 1, waist_y * 0.7),
            (quarter_waist, waist_y),
            (quarter_hips, hip_y),
            (quarter_hips + 5, hip_y + 30),
            (hem_width, total_length),
        ], tension=0.4)
        half_points.extend(side_seam[1:])
        
        # 5. Center hem
        half_points.append((0.0, total_length))
        
        # Mirror for full panel
        right_side = half_points[:-1]
        left_side = [(-x, y) for x, y in reversed(right_side[1:])]
        full_vertices = left_side + right_side + [(0.0, total_length)]
        
        self.pieces["Back_Bodice"] = PatternPiece(
            name="Back_Bodice",
            vertices=full_vertices,
            grainline_start=(0, armhole_depth),
            grainline_end=(0, total_length - 20),
        )
    
    def _draft_sleeves(self):
        """
        Draft sleeve pattern with smooth cap curve.
        
        FreeSewing sleeve caps use careful Bezier curves to ensure
        the cap ease distributes evenly when sewn into the armhole.
        """
        m = self.m
        
        # Sleeve measurements with abaya ease (generous fit)
        cap_height = m.chest / 5 + 3
        half_width = (m.biceps + m.sleeveEase) / 2
        wrist_half = (m.wrist + 10) / 2  # Wide cuffs for abaya
        
        sleeve_length = m.shoulderToWrist
        elbow_length = m.shoulderToElbow
        
        # === Build sleeve outline ===
        points = []
        
        # Cap curve: left edge → crown → right edge
        cap_left = (-half_width, cap_height)
        crown = (0.0, 0.0)
        cap_right = (half_width, cap_height)
        
        # Left cap curve (back of sleeve)
        left_cap = cubic_bezier(
            cap_left,
            (-half_width * 0.6, cap_height * 0.3),
            (-half_width * 0.2, 0.5),
            crown,
            steps=18
        )
        points.extend(left_cap)
        
        # Right cap curve (front of sleeve)
        right_cap = cubic_bezier(
            crown,
            (half_width * 0.2, 0.5),
            (half_width * 0.6, cap_height * 0.3),
            cap_right,
            steps=18
        )
        points.extend(right_cap[1:])  # Skip duplicate crown
        
        # Right side seam: cap → elbow → wrist
        # Slight taper toward wrist
        right_seam = smooth_curve_through_points([
            cap_right,
            (half_width - 1, elbow_length),
            (wrist_half, sleeve_length),
        ], tension=0.3)
        points.extend(right_seam[1:])
        
        # Wrist edge
        points.append((-wrist_half, sleeve_length))
        
        # Left side seam: wrist → elbow → cap
        left_seam = smooth_curve_through_points([
            (-wrist_half, sleeve_length),
            (-half_width + 1, elbow_length),
            cap_left,
        ], tension=0.3)
        points.extend(left_seam[1:-1])  # Skip endpoints (already in list)
        
        self.pieces["Left_Sleeve"] = PatternPiece(
            name="Left_Sleeve",
            vertices=points,
            grainline_start=(0, cap_height + 5),
            grainline_end=(0, sleeve_length - 10),
        )
        
        # Right sleeve is mirror of left
        mirrored = [(-x, y) for x, y in points]
        self.pieces["Right_Sleeve"] = PatternPiece(
            name="Right_Sleeve",
            vertices=mirrored,
            grainline_start=(0, cap_height + 5),
            grainline_end=(0, sleeve_length - 10),
        )


# =============================================================================
# SVG EXPORT - For visualization and external tools
# =============================================================================

class FreeSewingSVGExporter:
    """
    Export patterns to SVG format.
    
    SVG output can be used for:
    - Visual verification of patterns
    - Import into other CAD software
    - Printing for physical pattern making
    """
    
    def __init__(self, pieces: Dict[str, PatternPiece], scale: float = 10.0):
        """
        Args:
            pieces: Pattern pieces to export
            scale: cm to mm conversion (10.0 for mm output)
        """
        self.pieces = pieces
        self.scale = scale
    
    def export_combined(self, filepath: str) -> str:
        """Export all pieces to a single SVG file."""
        # Calculate layout
        padding = 50
        x_offset = padding
        max_height = 0
        piece_positions = []
        
        for piece in self.pieces.values():
            bounds = piece.get_bounds()
            w = (bounds[2] - bounds[0]) * self.scale
            h = (bounds[3] - bounds[1]) * self.scale
            piece_positions.append((x_offset, padding, bounds))
            x_offset += w + padding
            max_height = max(max_height, h)
        
        total_width = x_offset
        total_height = max_height + padding * 2
        
        # Create SVG
        svg = ET.Element("svg", {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": f"{total_width:.2f}mm",
            "height": f"{total_height:.2f}mm",
            "viewBox": f"0 0 {total_width:.2f} {total_height:.2f}",
        })
        
        # Add pieces
        for (piece_name, piece), (px, py, bounds) in zip(self.pieces.items(), piece_positions):
            g = ET.SubElement(svg, "g", {"id": piece_name})
            
            # Build path data
            path_data = self._vertices_to_path(piece.vertices, bounds, px, py)
            ET.SubElement(g, "path", {
                "d": path_data,
                "fill": "none",
                "stroke": "#000000",
                "stroke-width": "0.5",
            })
            
            # Add label
            label_x = px + (bounds[2] - bounds[0]) * self.scale / 2
            label_y = py + (bounds[3] - bounds[1]) * self.scale / 2
            text = ET.SubElement(g, "text", {
                "x": f"{label_x:.2f}",
                "y": f"{label_y:.2f}",
                "font-family": "Arial",
                "font-size": "10",
                "text-anchor": "middle",
                "fill": "#333333",
            })
            text.text = piece_name.replace("_", " ")
        
        # Write file
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        tree = ET.ElementTree(svg)
        tree.write(filepath, encoding="utf-8", xml_declaration=True)
        
        return filepath
    
    def _vertices_to_path(self, vertices: List[Tuple[float, float]], 
                          bounds: Tuple[float, float, float, float],
                          offset_x: float, offset_y: float) -> str:
        """Convert vertex list to SVG path data."""
        if not vertices:
            return ""
        
        min_x, min_y = bounds[0], bounds[1]
        
        commands = []
        for i, (x, y) in enumerate(vertices):
            sx = (x - min_x) * self.scale + offset_x
            sy = (y - min_y) * self.scale + offset_y
            cmd = "M" if i == 0 else "L"
            commands.append(f"{cmd} {sx:.2f},{sy:.2f}")
        
        commands.append("Z")
        return " ".join(commands)


# =============================================================================
# BLENDER INTEGRATION - Pattern data for cloth simulation
# =============================================================================

def generate_blender_pattern_data(size: str = "M", body_data: dict = None, arm_data: dict = None) -> dict:
    """
    Generate pattern data optimized for Blender cloth simulation.
    
    Can use either:
    - size: Standard size code (XS-XXL) for generic measurements
    - body_data + arm_data: Actual mannequin measurements for perfect fit

    Returns dict with:
    - size: Size code used
    - pieces: Dict of piece name → {vertices: [[x, y, z], ...]}
    - measurements: Body measurements used
    
    Vertex format: [x, 0, -y] in meters (Y=0 for flat panel, Z=height)
    """
    if body_data and arm_data:
        # Use actual mannequin measurements for perfect fit
        measurements = BodyMeasurements.from_mannequin_body(body_data, arm_data)
        print(f"  [FreeSewing] Using mannequin measurements (height={measurements.height:.0f}cm)")
    else:
        measurements = BodyMeasurements.from_size(size)
        print(f"  [FreeSewing] Using size {size} (height={measurements.height:.0f}cm)")
    drafter = AbayaDrafter(measurements)
    pieces = drafter.draft_all()
    
    result = {
        "size": size,
        "pieces": {},
        "measurements": {
            "chest": measurements.chest,
            "waist": measurements.waist,
            "hips": measurements.hips,
            "height": measurements.height,
        },
    }
    
    for name, piece in pieces.items():
        result["pieces"][name] = {
            "vertices": piece.to_blender_vertices(),
        }
    
    print(f"  [FreeSewing] Drafted {len(pieces)} pattern pieces (size {size})")
    for name, piece in pieces.items():
        bounds = piece.get_bounds()
        w = bounds[2] - bounds[0]
        h = bounds[3] - bounds[1]
        print(f"    {name}: {len(piece.vertices)} vertices, {w:.1f} x {h:.1f} cm")
    
    return result


def generate_abaya_pattern(size: str = "M", output_path: str = None) -> str:
    """
    Generate abaya pattern and export to SVG.
    
    Args:
        size: Standard size code
        output_path: Output SVG file path
    
    Returns:
        Path to created SVG file
    """
    measurements = BodyMeasurements.from_size(size)
    drafter = AbayaDrafter(measurements)
    pieces = drafter.draft_all()
    
    filepath = output_path or f"patterns/abaya_freesewing_{size}.svg"
    
    exporter = FreeSewingSVGExporter(pieces)
    result = exporter.export_combined(filepath)
    
    print(f"  [FreeSewing] Exported pattern to {result}")
    return result


# =============================================================================
# MAIN - Test pattern generation
# =============================================================================

if __name__ == "__main__":
    print("FreeSewing Pattern Generator - Abaya")
    print("=" * 50)
    
    # Generate for different sizes
    for size in ["S", "M", "L"]:
        print(f"\nGenerating size {size}...")
        
        # Generate Blender data
        data = generate_blender_pattern_data(size)
        print(f"  Pieces: {list(data['pieces'].keys())}")
        
        # Export SVG
        svg_path = generate_abaya_pattern(size, f"patterns/abaya_{size}.svg")
    
    print("\n" + "=" * 50)
    print("Pattern generation complete!")
