import re, html as html_mod

fname = '/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/372509240259.html'
with open(fname) as f:
    content = f.read()

sr_pattern = re.compile(r'<div id="Subreport(\d+)"')
sr_matches = list(sr_pattern.finditer(content))

SUBREPORT_FIELD_MAP = {
    2: ['servno1', 'underlinestr1', 'statustr1', 'newproductname1', 'newisclosed1',
        'typestr1', 'newproductidName1', 'newaddress1', 'newtelephone1', 'name1',
        'newstationidName1', 'newdealername1', 'newpresaledealername1'],
    7: ['buno1', 'transdate1', 'serno1', 'productcode1', 'productname1',
        'accountincustcustname1', 'accountoutcustcode1', 'accountoutcustname1', 'transstatus1'],
    8: ['buno1', 'transstockdate1', 'serno1', 'productcode1', 'productname1',
        'dealercustcode1', 'dealercustname1', 'distributorcustcode1',
        'distributorcustname1', 'type1', 'state1'],
}

for i, m in enumerate(sr_matches):
    sr_num = int(m.group(1))
    if sr_num not in SUBREPORT_FIELD_MAP:
        continue
    sr_start = m.start()
    sr_end = sr_matches[i+1].start() if i+1 < len(sr_matches) else len(content)
    sr_body = content[sr_start:sr_end]

    print(f'\n=== Subreport{sr_num} ===')
    record_count = 0
    for fid in SUBREPORT_FIELD_MAP[sr_num]:
        pattern = rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>'
        matches = re.findall(pattern, sr_body, re.DOTALL)
        vals = [html_mod.unescape(x).replace('\xa0',' ').strip() for x in matches]
        vals = [v for v in vals if v and v not in ['¥.00', '.00', '', ' ']]
        if vals:
            print(f'  {fid}: {vals}')
            record_count = max(record_count, len(vals))
    print(f'  Total records: {record_count}')
