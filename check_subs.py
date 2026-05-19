import re

fname = '/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/7162411250297.html'
with open(fname, 'r') as f:
    content = f.read()

# Extract Text labels and data IDs for each subreport
for sub_num in range(1, 11):
    sr_start = content.find(f'id="Subreport{sub_num}"')
    if sub_num < 10:
        sr_end = content.find(f'id="Subreport{sub_num+1}"')
    else:
        sr_end = len(content)
    sr_body = content[sr_start:sr_end]

    texts = re.findall(r'<div id="(Text\d+)"[^>]*>[^<]*<p[^>]*>[^<]*<span[^>]*>[^<]*<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL)
    data_ids = re.findall(r'id="([a-zA-Z0-9]+)"', sr_body)
    data_ids = [i for i in data_ids if not i.startswith('Text') and i.endswith('1')]

    labels = [(t, v.strip()) for t, v in texts]
    print(f'\n=== Subreport{sub_num} ===')
    print(f'  Labels: {labels}')
    print(f'  Data IDs ending with 1: {list(dict.fromkeys(data_ids))}')
