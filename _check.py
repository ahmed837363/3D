import ast
with open('blender_script.py', encoding='utf-8') as f:
    source = f.read()
tree = ast.parse(source)
lines = source.count('\n') + 1
print(f"Syntax OK — {lines} lines parsed successfully")

