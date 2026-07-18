content = open("hilo_HMI.py", "r").read()

# Las 3 ocurrencias son identicas: QGroupBox stylesheet con f" mezclado
# Patron: "QGroupBox{...}" seguido de f"font-weight..." 
content = content.replace(
    '"QGroupBox{border:1px solid #2A5090;border-radius:6px;margin-top:10px;"\n'
    '            f"font-weight:bold;color:#7AAFFF;font-size:9pt;}"\n'
    '            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px;}"',
    '"QGroupBox{border:1px solid #2A5090;border-radius:6px;margin-top:10px;'
    'font-weight:bold;color:#7AAFFF;font-size:9pt;}"\n'
    '            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px;}"'
)

open("hilo_HMI.py", "w").write(content)

import py_compile
try:
    py_compile.compile("hilo_HMI.py", doraise=True)
    remaining = content.count('f"font-weight:bold;color:#7AAFFF')
    print(f"OK — ocurrencias restantes: {remaining}")
except py_compile.PyCompileError as e:
    print(f"Error: {e}")
