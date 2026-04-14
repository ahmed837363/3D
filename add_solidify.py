import sys

with open(r"c:\Users\USER\Desktop\3D\blender_script.py", "r", encoding="utf-8") as f:
    text = f.read()

subsurf_code = "    subsurf.use_limit_surface = True"

if subsurf_code not in text:
    print("FAILED TO FIND SUBSURF")
    sys.exit(1)

new_code = subsurf_code + '''

    # As per tutorial: Solidify comes AFTER Subdivision Surface
    solidify = panel.modifiers.new(name="Thickness", type='SOLIDIFY')
    solidify.thickness = 0.002 # 2mm thick fabric
    solidify.offset = 0.0 # centered
    print(f"    {panel.name}: Added Solidify Modifier")'''

text = text.replace(subsurf_code, new_code)

with open(r"c:\Users\USER\Desktop\3D\blender_script.py", "w", encoding="utf-8") as f:
    f.write(text)

print("SUCCESSFULLY MODIFIED")
