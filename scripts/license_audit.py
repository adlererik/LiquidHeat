"""Inventory installed Python runtime licenses without importing applications."""
import importlib.metadata as md
import json
from pathlib import Path
items=[]
for d in sorted(md.distributions(),key=lambda x:x.metadata['Name'].lower()):
    m=d.metadata
    classifiers=[x for x in m.get_all('Classifier',[]) if x.startswith('License ::')]
    license_files=[str(x) for x in d.files or [] if 'license' in str(x).lower() or 'copying' in str(x).lower()]
    items.append({'name':m['Name'],'version':d.version,'license_expression':m.get('License-Expression'),
                  'license_metadata':m.get('License','')[:600],'license_classifiers':classifiers,'license_files':license_files})
print(json.dumps(items,indent=2))
