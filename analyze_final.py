import re

with open('/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/530231013H459.html', 'r') as f:
    content = f.read()

# Get text labels from each subreport's Text elements
for sub_num, sub_name in [(1,'装箱单'),(2,'服务单'),(3,'保卡扫描'),(4,'押金返还'),
                           (5,'设备档案'),(6,'库存调整单'),(7,'调拨单'),(8,'移库单'),
                           (9,'移机单'),(10,'库存条码明细')]:
    sr_start = content.find(f'id="Subreport{sub_num}"')
    if sub_num < 10:
        sr_end = content.find(f'id="Subreport{sub_num+1}"')
    else:
        sr_end = len(content)
    sr_body = content[sr_start:sr_end]

    texts = re.findall(r'<div id="(Text\d+)"[^>]*>[^<]*<p[^>]*>[^<]*<span[^>]*>[^<]*<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL)
    data_ids = re.findall(r'id="([a-zA-Z0-9]+1)"', sr_body)
    data_ids = [i for i in data_ids if not i.startswith('Text')]

    print(f'\n=== {sub_num}. {sub_name} ===')
    print(f'  Labels: {[(t, v.strip()) for t, v in texts[:25]]}')
    print(f'  Data IDs: {list(dict.fromkeys(data_ids))}')

    # Extract actual values for each unique data ID
    for fid in list(dict.fromkeys(data_ids)):
        m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL)
        if m:
            val = m.group(1).replace(chr(160), " ").strip()
            if val and val not in ['\xa0']:
                print(f'    {fid}: {val}')
