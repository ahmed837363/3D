import sys

with open(r"c:\Users\USER\Desktop\3D\blender_script.py", "r", encoding="utf-8") as f:
    text = f.read()

# REPLACEMENT 1: Function definition
func_start = 'def create_body_tube():'
func_end = '    return sleeve'
start_idx = text.find(func_start)
end_idx = text.find(func_end, start_idx) + len(func_end)

if start_idx == -1 or text.find(func_end, start_idx) == -1:
    print("FAILED TO FIND REPLACEMENT 1")
    sys.exit(1)

new_func = '''def create_tutorial_abaya():
    import bmesh
    mesh = bpy.data.meshes.new("Abaya")
    bm = bmesh.new()
    
    width = 0.82
    height = 1.45
    nx = 35
    nz = 50
    
    verts_front = {}
    verts_back = {}
    
    y_front = -0.12
    y_back = 0.12
    z_top = 1.45
    
    for ix in range(nx):
        x = (ix / (nx - 1)) * width
        for iz in range(nz):
            z = z_top - (iz / (nz - 1)) * height
            
            if x < 0.08 and z > 1.38: continue
            
            max_x_at_z = width
            if z < 0.95:
                max_x_at_z = 0.35 + (0.95 - z) * 0.1
                
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
        if vf.co.x < 0.01: continue
        if vf.co.z < z_top - height + 0.05: continue
        if vf.co.x < 0.1 and vf.co.z > 1.35: continue
        if vf.co.x > width - 0.05: continue
        
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

    bm.to_mesh(mesh)
    bm.free()
    
    obj = bpy.data.objects.new("Abaya_Tutorial", mesh)
    bpy.context.collection.objects.link(obj)
    
    mirror = obj.modifiers.new(name="Mirror", type='MIRROR')
    mirror.use_clip = True
    mirror.use_axis[0] = True
    
    max_z = max(v.co.z for v in mesh.vertices)
    pin_threshold = max_z - 0.03
    pin_group = obj.vertex_groups.new(name="Pin")
    top_verts = [v.index for v in mesh.vertices if v.co.z >= pin_threshold and abs(v.co.x) < 0.15]
    pin_group.add(top_verts, 1.0, 'ADD')
    
    print(f"Created tutorial abaya with {sew_count} sewing edges")
    return obj'''

text = text[:start_idx] + new_func + text[end_idx:]

# REPLACEMENT 2: Invocation
invoc_start = '        sys.stdout.flush()\n\n        print("PROGRESS:11%|Stage 1: Creating body tube...")'
invoc_end = '        fabric_panels = [body, l_sleeve, r_sleeve]'

start_idx = text.find(invoc_start)
end_idx = text.find(invoc_end, start_idx) + len(invoc_end)

if start_idx == -1 or text.find(invoc_end, start_idx) == -1:
    print("FAILED TO FIND REPLACEMENT 2")
    sys.exit(1)

new_invoc = '''        sys.stdout.flush()

        print("PROGRESS:11%|Creating tutorial abaya...")
        sys.stdout.flush()
        abaya = create_tutorial_abaya()
        fabric_panels = [abaya]'''

text = text[:start_idx] + new_invoc + text[end_idx:]

# REPLACEMENT 3: Physics logic update (Make sewing run for everywhere!)
phys_start = '    if PATTERN_SOURCE == "freesewing":\n        cloth.settings.compression_stiffness = 0.0'
phys_end = '        if hasattr(cloth.settings, sew_attr):'

start_idx = text.find(phys_start)
end_idx = text.find(phys_end, start_idx) + len(phys_end)

if start_idx == -1 or text.find(phys_end, start_idx) == -1:
    print("FAILED TO FIND REPLACEMENT 3")
    sys.exit(1)

new_phys = '''    # All patterns use sewing now!
    cloth.settings.compression_stiffness = 0.0
    cloth.settings.compression_damping = 0.0

    # Enable sewing: naked edges between panels act as invisible threads
    sewing_enabled = False
    for sew_attr in ['use_sewing_springs', 'use_sewing']:
        if hasattr(cloth.settings, sew_attr):'''

text = text[:start_idx] + new_phys + text[end_idx:]

# Remove self collision disabling for freesewing exclusively
scol_start = '    if PATTERN_SOURCE == "freesewing":\n        cloth.collision_settings.use_self_collision = False'
scol_end = '        cloth.collision_settings.self_friction = CLOTH_PARAMS["self_friction"]'

start_idx = text.find(scol_start)
end_idx = text.find(scol_end, start_idx) + len(scol_end)

if start_idx == -1 or text.find(scol_end, start_idx) == -1:
    print("FAILED TO FIND REPLACEMENT 4")
    sys.exit(1)
    
new_scol = '''    # Tutorial specifically enabled self collisions!
    cloth.collision_settings.use_self_collision = True
    cloth.collision_settings.self_distance_min = 0.001
    cloth.collision_settings.self_friction = CLOTH_PARAMS["self_friction"]'''

text = text[:start_idx] + new_scol + text[end_idx:]

with open(r"c:\Users\USER\Desktop\3D\blender_script.py", "w", encoding="utf-8") as f:
    f.write(text)

print("SUCCESSFULLY MODIFIED")
