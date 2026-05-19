import re, html as html_mod

with open('/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/7132408080810.html') as f:
    content = f.read()

sr_pattern = re.compile(r'<div id="Subreport(\d+)"')
sr_matches = list(sr_pattern.finditer(content))

SUBREPORT_FIELD_MAP = {
    1: ['SHIPSTATUS1', 'zxd1', 'shipdate1', 'newerpshipno1', 'ProductNumber1',
        'newproductidName1', 'newordsalesorderidName1', 'newname1', 'newproductname1',
        'newtype1', 'newdeposit1', 'newaccountidName1', 'newpayaccountidName1'],
    2: ['servno1', 'underlinestr1', 'statustr1', 'newproductname1', 'newisclosed1',
        'typestr1', 'newproductidName1', 'newaddress1', 'newtelephone1', 'name1',
        'newstationidName1', 'newdealername1', 'newpresaledealername1'],
    5: ['newname1', 'newproductname1', 'productname1', 'instlled1', 'returned1',
        'newdeposit1', 'isonline1', 'myproductdealer1', 'productdealernumber1',
        'depositdealernumber1', 'depositdealer1', 'customer1', 'newphone1',
        'newaddress1', 'installdate1'],
    8: ['buno1', 'productcode1', 'productname1', 'serno1', 'type1', 'state1',
        'dealercustcode1', 'dealercustname1', 'distributorcustcode1',
        'distributorcustname1', 'transstockdate1'],
    10: ['newname1', 'newproductname1', 'newproductidName1', 'newstatus1',
         'statuscode1', 'dealername1', 'newdeposit1'],
}

fields = {}
for i, m in enumerate(sr_matches):
    sr_num = int(m.group(1))
    if sr_num not in SUBREPORT_FIELD_MAP:
        continue
    sr_start = m.start()
    sr_end = sr_matches[i+1].start() if i+1 < len(sr_matches) else len(content)
    sr_body = content[sr_start:sr_end]

    for fid in SUBREPORT_FIELD_MAP[sr_num]:
        if fid in fields:
            continue
        pattern = rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>'
        match = re.search(pattern, sr_body, re.DOTALL)
        if match:
            value = html_mod.unescape(match.group(1)).strip()
            if value and value not in ['¥.00', '.00', '', ' ']:
                fields[fid] = value
                print(f'Subreport{sr_num} {fid}: [{value}]')

print('\n--- 最终 fields ---')
for k, v in sorted(fields.items()):
    print(f'{k}: {v}')
