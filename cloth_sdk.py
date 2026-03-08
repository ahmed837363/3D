"""
ClothSDK — Cloth Simulation SDK for Abaya3D Maker

Defines fabric material presets and physics parameters for realistic
cloth draping simulation on mannequins.

Usage:
    from cloth_sdk import get_fabric, get_fabric_names, get_fabric_keys

    fabric = get_fabric("silk")
    blender_params = fabric.to_blender_params()
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class FabricMaterial:
    """Physical properties of a fabric for cloth simulation."""

    name: str
    display_name: str

    # Surface density (kg/m²)
    mass: float

    # Structural stiffness
    tension_stiffness: float
    compression_stiffness: float

    # Bending
    bending_stiffness: float

    # Damping
    tension_damping: float
    compression_damping: float
    bending_damping: float

    # Collision
    friction: float = 5.0
    self_friction: float = 5.0
    collision_distance: float = 0.010
    self_collision_distance: float = 0.008
    collision_quality: int = 5

    # Simulation quality (substeps per frame)
    quality_steps: int = 10

    # Drape info (0 = stiff board, 1 = fluid drape)
    drape_coefficient: float = 0.5

    # Rendering hints for Blender material
    roughness: float = 0.7
    sheen: float = 0.0
    transmission: float = 0.0

    description: str = ""

    def to_blender_params(self) -> dict:
        """Export parameters for Blender cloth modifier and material."""
        return {
            "mass": self.mass,
            "tension_stiffness": self.tension_stiffness,
            "compression_stiffness": self.compression_stiffness,
            "bending_stiffness": self.bending_stiffness,
            "tension_damping": self.tension_damping,
            "compression_damping": self.compression_damping,
            "bending_damping": self.bending_damping,
            "friction": self.friction,
            "self_friction": self.self_friction,
            "collision_distance": self.collision_distance,
            "self_collision_distance": self.self_collision_distance,
            "collision_quality": self.collision_quality,
            "quality_steps": self.quality_steps,
            "roughness": self.roughness,
            "sheen": self.sheen,
            "transmission": self.transmission,
        }


# ---------------------------------------------------------------------------
# Fabric presets — tuned for abaya garment simulation
# ---------------------------------------------------------------------------
FABRICS: Dict[str, FabricMaterial] = {}


def _register(fabric: FabricMaterial):
    FABRICS[fabric.name] = fabric


_register(FabricMaterial(
    name="silk",
    display_name="Silk",
    mass=0.15,
    tension_stiffness=5.0,
    compression_stiffness=5.0,
    bending_stiffness=0.005,
    tension_damping=5.0,
    compression_damping=5.0,
    bending_damping=0.5,
    friction=5.0,
    self_friction=5.0,
    drape_coefficient=0.85,
    roughness=0.35,
    sheen=0.5,
    description="Lightweight, fluid drape with lustrous sheen",
))

_register(FabricMaterial(
    name="chiffon",
    display_name="Chiffon",
    mass=0.08,
    tension_stiffness=2.0,
    compression_stiffness=2.0,
    bending_stiffness=0.01,
    tension_damping=3.0,
    compression_damping=3.0,
    bending_damping=0.3,
    friction=3.0,
    self_friction=3.0,
    drape_coefficient=0.95,
    roughness=0.4,
    transmission=0.3,
    description="Sheer, ultra-lightweight with flowing drape",
))

_register(FabricMaterial(
    name="crepe",
    display_name="Crepe",
    mass=0.25,
    tension_stiffness=15.0,
    compression_stiffness=15.0,
    bending_stiffness=0.02,
    tension_damping=8.0,
    compression_damping=8.0,
    bending_damping=2.0,
    friction=8.0,
    self_friction=5.0,
    drape_coefficient=0.6,
    roughness=0.6,
    sheen=0.2,
    description="Textured surface with moderate drape and body",
))

_register(FabricMaterial(
    name="cotton",
    display_name="Cotton",
    mass=0.30,
    tension_stiffness=20.0,
    compression_stiffness=20.0,
    bending_stiffness=0.05,
    tension_damping=10.0,
    compression_damping=10.0,
    bending_damping=3.0,
    friction=10.0,
    self_friction=8.0,
    drape_coefficient=0.4,
    roughness=0.75,
    sheen=0.1,
    description="Natural, structured fabric with crisp drape",
))

_register(FabricMaterial(
    name="velvet",
    display_name="Velvet",
    mass=0.50,
    tension_stiffness=25.0,
    compression_stiffness=25.0,
    bending_stiffness=0.1,
    tension_damping=15.0,
    compression_damping=15.0,
    bending_damping=5.0,
    friction=20.0,
    self_friction=15.0,
    drape_coefficient=0.35,
    roughness=0.9,
    sheen=0.8,
    description="Heavy, luxurious pile fabric with rich drape",
))

_register(FabricMaterial(
    name="nida",
    display_name="Nida",
    mass=0.20,
    tension_stiffness=12.0,
    compression_stiffness=12.0,
    bending_stiffness=0.015,
    tension_damping=7.0,
    compression_damping=7.0,
    bending_damping=1.5,
    friction=6.0,
    self_friction=5.0,
    drape_coefficient=0.7,
    roughness=0.55,
    sheen=0.15,
    description="Classic abaya fabric — matte, elegant drape",
))

_register(FabricMaterial(
    name="jersey",
    display_name="Jersey",
    mass=0.22,
    tension_stiffness=8.0,
    compression_stiffness=8.0,
    bending_stiffness=0.008,
    tension_damping=6.0,
    compression_damping=6.0,
    bending_damping=1.0,
    friction=8.0,
    self_friction=6.0,
    drape_coefficient=0.8,
    roughness=0.65,
    sheen=0.1,
    description="Stretchy knit with body-hugging drape",
))

_register(FabricMaterial(
    name="satin",
    display_name="Satin",
    mass=0.18,
    tension_stiffness=6.0,
    compression_stiffness=6.0,
    bending_stiffness=0.003,
    tension_damping=4.0,
    compression_damping=4.0,
    bending_damping=0.5,
    friction=3.0,
    self_friction=2.0,
    drape_coefficient=0.88,
    roughness=0.2,
    sheen=0.7,
    description="Glossy, smooth fabric with liquid drape",
))

_register(FabricMaterial(
    name="organza",
    display_name="Organza",
    mass=0.06,
    tension_stiffness=18.0,
    compression_stiffness=18.0,
    bending_stiffness=0.15,
    tension_damping=5.0,
    compression_damping=5.0,
    bending_damping=2.0,
    friction=3.0,
    self_friction=2.0,
    drape_coefficient=0.2,
    roughness=0.3,
    transmission=0.5,
    description="Crisp, sheer fabric that holds its shape",
))

_register(FabricMaterial(
    name="linen",
    display_name="Linen",
    mass=0.28,
    tension_stiffness=22.0,
    compression_stiffness=22.0,
    bending_stiffness=0.08,
    tension_damping=12.0,
    compression_damping=12.0,
    bending_damping=4.0,
    friction=12.0,
    self_friction=10.0,
    drape_coefficient=0.3,
    roughness=0.8,
    sheen=0.05,
    description="Structured natural fabric with stiff drape",
))


def get_fabric(name: str) -> FabricMaterial:
    """Get a fabric preset by name. Falls back to silk."""
    return FABRICS.get(name, FABRICS["silk"])


def get_fabric_names() -> List[str]:
    """Return display names of all registered fabrics."""
    return [f.display_name for f in FABRICS.values()]


def get_fabric_keys() -> List[str]:
    """Return internal keys of all registered fabrics."""
    return list(FABRICS.keys())
