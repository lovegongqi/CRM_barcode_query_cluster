import re, html as html_mod

with open('/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/7132408080810.html') as f:
    content = f.read()

for sub_num in range(1, 11):
    sr_start = content.find(f'id="Subreport{sub_num}"')
    if sub_num < 10:
        sr_end = content.find(f'id="Subreport{sub_num+1}"')
    else:
        sr_end = len(content)
    sr_body = content[sr_start:sr_end]

    # Find all field divs with their values
    field_data = {}
    for fm in re.finditer(r'<div id="([a-zA-Z0-9]+)"', sr_body):
        fid = fm.group(1)
        if fid.startswith('Text') or fid.startswith('Subreport') or fid.startswith('Page') or fid.startswith('Report'):
            continue
        if fid in field_data:
            continue
        chunk = sr_body[fm.start():]
        close_m = re.search(r'</div>', chunk)
        if close_m == -1:
            continue
        div_content = chunk[:close_m.start()]
        span_m = re.search(r'<span[^>]*>([^<]+)</span>', div_content)
        if span_m:
            val = html_mod.unescape(span_m.group(1)).replace('\xa0', ' ').strip()
            if val and val not in ['¥.00', '.00', '', ' ']:
                field_data[fid] = val

    # Find labels (Text divs)
    label_data = {}
    for tm in re.finditer(r'<div id="(Text\d+)"[^>]*>[^<]*<p[^>]*>[^<]*<span[^>]*>[^<]*<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL):
        tid = tm.group(1)
        label = html_mod.unescape(tm.group(2)).replace('\xa0', ' ').strip()
        if label:
            label_data[tid] = label

    print(f'\n=== Subreport{sub_num} ===')
    if not field_data:
        print('  （无数据）')
    else:
        for fid, val in sorted(field_data.items()):
            print(f'  {fid}: {val}')
