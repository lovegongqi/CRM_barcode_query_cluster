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

    # Parse all elements (label + field pairs) in order
    # Text divs are labels, field divs (ending with "1") are values
    pairs = []
    seen_fields = set()

    # Find all elements in order
    all_elements = []
    for tm in re.finditer(r'<div id="(Text\d+)"', sr_body):
        all_elements.append(('label', tm.start(), tm.group(1)))
    for fm in re.finditer(r'<div id="([a-zA-Z0-9]+)"', sr_body):
        fid = fm.group(1)
        if not (fid.startswith('Text') or fid.startswith('Subreport') or fid.startswith('Page') or fid.startswith('Report')):
            all_elements.append(('field', fm.start(), fid))

    all_elements.sort(key=lambda x: x[1])

    print(f'\n=== Subreport{sub_num} ===')
    if not all_elements:
        print('  （无数据）')
    else:
        for etype, pos, val in all_elements:
            if etype == 'label':
                # Get the label text
                chunk = sr_body[pos:]
                close_m = re.search(r'</div>', chunk)
                if close_m:
                    div_content = chunk[:close_m.start()]
                    span_m = re.search(r'<span[^>]*>([^<]+)</span>', div_content)
                    if span_m:
                        label_text = html_mod.unescape(span_m.group(1)).replace('\xa0', ' ').strip()
                        if label_text:
                            print(f'  标签 [{val}] = "{label_text}"')
            else:
                if val in seen_fields:
                    continue
                seen_fields.add(val)
                chunk = sr_body[pos:]
                close_m = re.search(r'</div>', chunk)
                if close_m:
                    div_content = chunk[:close_m.start()]
                    span_m = re.search(r'<span[^>]*>([^<]+)</span>', div_content)
                    if span_m:
                        fval = html_mod.unescape(span_m.group(1)).replace('\xa0', ' ').strip()
                        if fval and fval not in ['¥.00', '.00', '', ' ']:
                            print(f'  字段 [{val}] = "{fval}"')
