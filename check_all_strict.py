import re, html as html_mod

with open('/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/7132408080810.html') as f:
    content = f.read()

sr_pattern = re.compile(r'<div id="Subreport(\d+)"')
sr_matches = list(sr_pattern.finditer(content))

for i, m in enumerate(sr_matches):
    sr_num = m.group(1)
    sr_start = m.start()
    sr_end = sr_matches[i+1].start() if i+1 < len(sr_matches) else len(content)
    sr_body = content[sr_start:sr_end]

    print(f'\n=== Subreport{sr_num} ===')

    # Find all field divs
    seen_ids = set()
    for fm in re.finditer(r'<div id="([a-zA-Z0-9]+)"', sr_body):
        fid = fm.group(1)
        if fid.startswith('Text') or fid.startswith('Subreport') or fid.startswith('Page') or fid.startswith('Report'):
            continue
        if fid in seen_ids:
            continue
        seen_ids.add(fid)

        # Extract the span value from this div
        div_start_in_sr = fm.start()
        div_chunk = sr_body[div_start_in_sr:]

        # Find the closing </div> that belongs to this div (match nested properly)
        # Use the first </div> after the opening
        first_close = div_chunk.find('</div>')
        if first_close == -1:
            continue
        div_content = div_chunk[:first_close]

        span_m = re.search(r'<span[^>]*>([^<]+)</span>', div_content)
        if span_m:
            val = html_mod.unescape(span_m.group(1)).replace('\xa0', ' ').strip()
            if val and val not in ['¥.00', '.00', '', ' ']:
                print(f'  {fid}: {val}')
