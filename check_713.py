import re, html as html_mod

fname = '/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/7132408080810.html'
with open(fname) as f:
    content = f.read()

for sub_num in range(1, 11):
    sr_start = content.find(f'id="Subreport{sub_num}"')
    if sub_num < 10:
        sr_end = content.find(f'id="Subreport{sub_num+1}"')
    else:
        sr_end = len(content)
    sr_body = content[sr_start:sr_end]

    texts = re.findall(r'<div id="(Text\d+)"[^>]*>[^<]*<p[^>]*>[^<]*<span[^>]*>[^<]*<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL)
    data_ids = re.findall(r'id="([a-zA-Z0-9]+)"', sr_body)
    data_ids = [i for i in data_ids if not i.startswith('Text') and i not in [f'Subreport{j}' for j in range(1,11)] and not i.startswith('Page') and not i.startswith('Report')]
    labels = [(t, v.strip()) for t, v in texts]
    unique_ids = list(dict.fromkeys(data_ids))

    print(f'\n=== Subreport{sub_num} ===')
    print(f'Labels: {labels}')
    print(f'Data IDs: {unique_ids}')

    for fid in unique_ids:
        m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL)
        if m:
            val = html_mod.unescape(m.group(1)).replace('\xa0',' ').strip()
            print(f'  {fid}: [{val}]')
