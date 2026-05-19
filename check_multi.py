import re, html as html_mod

for fname in ['3402405250082.html', '372509240259.html', '1332104080877.html']:
    with open(f'/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/{fname}') as f:
        content = f.read()

    sr_pattern = re.compile(r'<div id="Subreport(\d+)"')
    sr_matches = list(sr_pattern.finditer(content))

    buno_ids = set()
    servno_ids = set()

    for i, m in enumerate(sr_matches):
        sr_num = m.group(1)
        sr_start = m.start()
        sr_end = sr_matches[i+1].start() if i+1 < len(sr_matches) else len(content)
        sr_body = content[sr_start:sr_end]

        b = re.findall(r'id="(buno\d+)"', sr_body)
        s = re.findall(r'id="(servno\d+)"', sr_body)
        if b:
            buno_ids.update(b)
        if s:
            servno_ids.update(s)

    print(f'\n=== {fname} ===')
    print(f'  buno IDs: {sorted(buno_ids)}')
    print(f'  servno IDs: {sorted(servno_ids)}')

    for bid in sorted(buno_ids):
        for i, m in enumerate(sr_matches):
            sr_start = m.start()
            sr_end = sr_matches[i+1].start() if i+1 < len(sr_matches) else len(content)
            sr_body = content[sr_start:sr_end]
            bm = re.search(rf'id="{bid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL)
            if bm:
                val = html_mod.unescape(bm.group(1)).replace('\xa0',' ').strip()
                if val and val not in ['¥.00', '.00', '', ' ']:
                    print(f'  {bid} (Subreport{m.group(1)}): {val}')

    for sid in sorted(servno_ids):
        for i, m in enumerate(sr_matches):
            sr_start = m.start()
            sr_end = sr_matches[i+1].start() if i+1 < len(sr_matches) else len(content)
            sr_body = content[sr_start:sr_end]
            sm = re.search(rf'id="{sid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL)
            if sm:
                val = html_mod.unescape(sm.group(1)).replace('\xa0',' ').strip()
                if val and val not in ['¥.00', '.00', '', ' ']:
                    print(f'  {sid} (Subreport{m.group(1)}): {val}')
