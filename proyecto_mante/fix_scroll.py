content = open("hilo_HMI.py", "r").read()

# Renombrar el scroll interno de diagnóstico para que no pise el externo
content = content.replace(
    '''        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(180)
        scroll.setStyleSheet(''',
    '''        scroll_diag = QScrollArea()
        scroll_diag.setWidgetResizable(True)
        scroll_diag.setFixedHeight(180)
        scroll_diag.setStyleSheet('''
)
content = content.replace(
    '''        scroll.setWidget(self.lbl_historial)
        diag_lay.addWidget(scroll)''',
    '''        scroll_diag.setWidget(self.lbl_historial)
        diag_lay.addWidget(scroll_diag)'''
)

open("hilo_HMI.py", "w").write(content)

import py_compile
try:
    py_compile.compile("hilo_HMI.py", doraise=True)
    print("OK — sintaxis correcta")
except py_compile.PyCompileError as e:
    print(f"Error: {e}")
