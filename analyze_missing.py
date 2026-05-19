import re

with open('/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/530231013H459.html', 'r') as f:
    content = f.read()

for sub_num, sub_name in [(3,'保卡扫描'),(4,'押金返还'),(6,'库存调整单'),(9,'移机单')]:
    sr_start = content.find(f'id="Subreport{sub_num}"')
    sr_end = content.find(f'id="Subreport{sub_num+1}"')
    sr_body = content[sr_start:sr_end]
    all_ids = re.findall(r'id="([a-zA-Z0-9]+)"', sr_body)
    unique_ids = list(dict.fromkeys(all_ids))
    data_ids = [i for i in unique_ids if not i.startswith('Text') and not i.startswith('Subreport') and not i.startswith('Page') and not i.startswith('Report')]
    print(f'\n=== Subreport{sub_num}({sub_name}) data IDs: {data_ids} ===')
    for fid in data_ids:
        m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr_body, re.DOTALL)
        if m:
            print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')

# Also check all remaining field IDs in the whole file
print('\n\n=== All remaining field IDs not yet confirmed ===')
known_ids = ['newisclosed1','SHIPSTATUS1','instlled1','newstatus1','typestr1','statustr1',
'newname1','zxd1','shipdate1','newerpshipno1','ProductNumber1','newproductidName1',
'newordsalesorderidName1','servno1','name1','newaddress1','newtelephone1','newstationidName1',
'newdealername1','dealername1','buno1','transstockdate1','newproductname1','newaccountidName1',
'newtype1','newdeposit1','returned1','newpresaledealername1','underlinestr1','productname1',
'productdealernumber1','depositdealer1','depositdealernumber1','isonline1',
'serno1','productcode1','dealercustcode1','dealercustname1','distributorcustcode1',
'distributorcustname1','type1','state1','statuscode1']

all_file_ids = re.findall(r'id="([a-zA-Z0-9]+)"', content)
all_unique = list(dict.fromkeys(all_file_ids))
for fid in all_unique:
    if fid not in ['Subreport1','Subreport2','Subreport3','Subreport4','Subreport5',
                   'Subreport6','Subreport7','Subreport8','Subreport9','Subreport10',
                   'PageHeaderSection1','PageFooterSection1','PageFooterSection2','ReportFooterSection1'] \
       and not fid.startswith('Text') and not fid.startswith('Field') \
       and fid not in known_ids:
        m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', content, re.DOTALL)
        if m:
            val = m.group(1).replace(chr(160)," ").strip()
            if val and val not in ['¥.00', '.00', '***', '', ' ']:
                print(f'  {fid}: {val}')
