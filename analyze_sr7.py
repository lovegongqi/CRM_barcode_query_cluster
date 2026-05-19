import re

with open('/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/530231013H459.html', 'r') as f:
    content = f.read()

sr7_start = content.find('id="Subreport7"')
sr7_end = content.find('id="Subreport8"')
sr7_body = content[sr7_start:sr7_end]

for fid in ['buno1', 'transdate1', 'serno1', 'productcode1', 'productname1',
            'accountincustcode1', 'accountincustcustname1', 'accountoutcustcode1', 'accountoutcustname1', 'transstatus1']:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr7_body, re.DOTALL)
    if m:
        print(f'Subreport7 {fid}: {m.group(1).replace(chr(160)," ").strip()}')
    else:
        print(f'Subreport7 {fid}: NOT FOUND')

print('\n--- Whole file ---')
for fid in ['buno1', 'transdate1', 'serno1', 'productcode1', 'accountincustcustname1', 'transstatus1']:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', content, re.DOTALL)
    if m:
        print(f'Whole file {fid}: {m.group(1).replace(chr(160)," ").strip()}')
    else:
        print(f'Whole file {fid}: NOT FOUND')

# Check what Text1 label in Subreport7 actually contains
m = re.search(r'<div id="Text1"[^>]*>[^<]*<p[^>]*>[^<]*<span[^>]*>[^<]*<span[^>]*>([^<]+)</span>', sr7_body, re.DOTALL)
if m:
    print(f'\nSubreport7 Text1 label: {m.group(1)}')

# Also check transstockdate1
m2 = re.search(r'id="transstockdate1"[^>]*>.*?<span[^>]*>([^<]+)</span>', content, re.DOTALL)
if m2:
    print(f'\ntransstockdate1: {m2.group(1).replace(chr(160)," ").strip()}')
else:
    print('\ntransstockdate1: NOT FOUND')
