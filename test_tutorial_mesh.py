import bpy
import bmesh
import math
import sys

# Clear scene
bpy.ops.wm.read_factory_settings(use_empty=True)

def create_tutorial_abaya():
    mesh = bpy.data.meshes.new("Abaya")
    bm = bmesh.new()
    
    width = 0.82  # Center to end of sleeve
    height = 1.45 # From shoulder down to floor
    nx = 35 # Number of segments horizontally
    nz = 50 # Number of segments vertically
    
    verts_front = {}
    verts_back = {}
    
    y_front = -0.12
    y_back = 0.12
    z_top = 1.45
    
    for ix in range(nx):
        x = (ix / (nx - 1)) * width
        for iz in range(nz):
            z = z_top - (iz / (nz - 1)) * height
            
            # Cut neck
            if x < 0.08 and z > 1.38: continue
            
            # Cut underarm (sleeve is wide)
            # Create a nice curved taper from underarm down to waist
            # Instead of a sharp cut, let's define waist x:
            max_x_at_z = width
            if z < 0.95:
                # scale x down below waist
                max_x_at_z = 0.35 + (0.95 - z) * 0.1 # tapers out slightly towards bottom
            else:
                # Sleeve arm hole tapers up from 0.95 to 1.45
                pass
                
            if x > max_x_at_z: continue
            
            vf = bm.verts.new((x, y_front, z))
            verts_front[(ix, iz)] = vf
            
            vb = bm.verts.new((x, y_back, z))
            verts_back[(ix, iz)] = vb
            
    bm.verts.ensure_lookup_table()
    
    for ix in range(nx - 1):
        for iz in range(nz - 1):
            if (ix, iz) in verts_front and (ix+1, iz) in verts_front and (ix, iz+1) in verts_front and (ix+1, iz+1) in verts_front:
                try: bm.faces.new([verts_front[(ix, iz)], verts_front[(ix+1, iz)], verts_front[(ix+1, iz+1)], verts_front[(ix, iz+1)]])
                except: pass
            if (ix, iz) in verts_back and (ix+1, iz) in verts_back and (ix, iz+1) in verts_back and (ix+1, iz+1) in verts_back:
                try: bm.faces.new([verts_back[(ix, iz)], verts_back[(ix, iz+1)], verts_back[(ix+1, iz+1)], verts_back[(ix+1, iz)]])
                except: pass

    bm.edges.ensure_lookup_table()
    b_edges_f = [e for e in bm.edges if len(e.link_faces) <= 1 and e.verts[0].co.y == y_front]
    b_edges_b = [e for e in bm.edges if len(e.link_faces) <= 1 and e.verts[0].co.y == y_back]
    
    vf_bound = set()
    vb_bound = set()
    for e in b_edges_f: vf_bound.update(e.verts)
    for e in b_edges_b: vb_bound.update(e.verts)
        
    sew_count = 0
    for vf in vf_bound:
        if vf.co.x < 0.01: continue # Center seam
        if vf.co.z < z_top - height + 0.05: continue # Bottom hem
        if vf.co.x < 0.1 and vf.co.z > 1.35: continue # Neck hole
        if vf.co.x > width - 0.05: continue # Wrist opening
        
        best_vb = None
        best_dist = 999
        for vb in vb_bound:
            d = (vf.co.x - vb.co.x)**2 + (vf.co.z - vb.co.z)**2
            if d < best_dist:
                best_dist = d
                best_vb = vb
        
        if best_vb and best_dist < 0.01:
            try:
                bm.edges.new([vf, best_vb])
                sew_count += 1
            except: pass

    print(f"Created {sew_count} sewing edges")

    bm.to_mesh(mesh)
    bm.free()
    
    obj = bpy.data.objects.new("Abaya_Tutorial", mesh)
    bpy.context.collection.objects.link(obj)
    
    return obj

obj = create_tutorial_abaya()
bpy.ops.wm.save_as_mainfile(filepath=r"c:\Users\USER\Desktop\3D\test_tutorial.blend")
print("SUCCESS")
