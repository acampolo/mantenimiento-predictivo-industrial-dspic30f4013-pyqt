content = open("hilo_HMI.py", "r").read()

# Reemplazar el stylesheet problemático de QGroupBox en _crear_tab_rul
old = '''        grp_placa.setStyleSheet(
            f"QGroupBox{{border:1px solid #2A5090;border-radius:6px;margin-top:10px;"
            f"font-weight:bold;color:#7AAFFF;font-size:9pt;}}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 5px;}}"
        )'''
new = '''        grp_placa.setStyleSheet(
            "QGroupBox{border:1px solid #2A5090;border-radius:6px;margin-top:10px;"
            "font-weight:bold;color:#7AAFFF;font-size:9pt;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px;}"
        )'''
content = content.replace(old, new)

# Reemplazar todos los QGroupBox stylesheet con f-string en el archivo
import re
# Patron: fstrings con {{ }} en setStyleSheet de QGroupBox
content = content.replace(
    '"QGroupBox{{border:1px solid #2A5090',
    '"QGroupBox{border:1px solid #2A5090'
)
content = content.replace('border-radius:6px;margin-top:10px;}"', 'border-radius:6px;margin-top:10px;}')
content = content.replace('font-weight:bold;color:#7AAFFF;font-size:9pt;}}', 'font-weight:bold;color:#7AAFFF;font-size:9pt;}')
content = content.replace('f"QGroupBox{{', '"QGroupBox{')
content = content.replace('f"QGroupBox::title{{', '"QGroupBox::title{')
content = content.replace('subcontrol-origin:margin;left:10px;padding:0 5px;}}', 'subcontrol-origin:margin;left:10px;padding:0 5px;}')

open("hilo_HMI.py", "w").write(content)
print("OK")
