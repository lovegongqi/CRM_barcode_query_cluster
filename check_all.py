import re

for fname, label in [
    ('530231013H459.html', '530231013H459'),
    ('7162411250297.html', '7162411250297')
]:
    with open(f'/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/{fname}') as f:
        content = f.read()

    for sub_num in [5, 7, 8]:
        sr_start = content.find(f'id="Subreport{sub_num}"')
        sr_end = content.find(f'id="Subreport{sub_num+1}"')
        sr_body = content[sr_start:sr_end]
        data_ids = re.findall(r'id="([a-zA-Z0-9]+)"', sr_body)
        data_ids = [i for i in data_ids if not i.startswith('Text') and i not in ['Subreport1','Subreport2','Subreport3','Subreport4','Subreport5','Subreport6','Subreport7','Subreport8','Subreport9','Subreport10']]
        print(f'{label} Subreport{sub_num}: {list(dict.fromkeys(data_ids))}')

        for fid in list(dict.fromkeys(data_ids)):
            m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL)
            if m:
                val = m.group(1).replace('\xa0',' ').strip()
                if val and val not in ['¥.00', '.00', '***', '', ' ']:
                    print(f'  {fid}: {val[:60]}')
