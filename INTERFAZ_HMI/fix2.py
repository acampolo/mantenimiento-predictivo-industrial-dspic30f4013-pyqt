import re

content = open("hilo_HMI.py", "r").read()

# Reemplazar TODOS los f-strings de QGroupBox stylesheet de una vez
# Patron: f"QGroupBox{{...}}..." -> "QGroupBox{...}..."
content = re.sub(
    r'f"QGroupBox\{\{([^}]*)\}\}([^"]*)"',
    r'"QGroupBox{\1}\2"',
    content
)
content = re.sub(
    r'f"QGroupBox::title\{\{([^}]*)\}\}([^"]*)"',
    r'"QGroupBox::title{\1}\2"',
    content
)

# Limpiar cualquier f" que quedó suelto en stylesheets de QGroupBox
content = content.replace(
    'f"QGroupBox{border:1px solid #2A5090',
    '"QGroupBox{border:1px solid #2A5090'
)
content = content.replace(
    'f"QGroupBox::title{subcontrol-origin',
    '"QGroupBox::title{subcontrol-origin'
)

open("hilo_HMI.py", "w").write(content)
print("OK — revisa linea 883:")
# Mostrar contexto del error anterior
lines = content.split('\n')
for i, l in enumerate(lines[879:889], start=880):
    print(f"{i}: {l}")
