content = open("hilo_HMI.py", "r").read()

# Quitar f" de lineas que no tienen variables {}, solo texto fijo
import re

# Buscar f"texto}" donde no hay { antes del } — significa f-string sin variable
content = re.sub(r'\bf("(?:[^"{])*})', r'\1', content)

open("hilo_HMI.py", "w").write(content)

import py_compile
try:
    py_compile.compile("hilo_HMI.py", doraise=True)
    print("OK — sintaxis correcta")
except py_compile.PyCompileError as e:
    print(f"Error en linea: {e}")
