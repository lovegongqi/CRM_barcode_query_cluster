import re

with open('/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/530231013H459.html', 'r') as f:
    content = f.read()

FIELD_IDS = ['newisclosed1','SHIPSTATUS1','instlled1','newstatus1','typestr1','statustr1',
'newname1','zxd1','shipdate1','newerpshipno1','ProductNumber1','newproductidName1',
'newordsalesorderidName1','servno1','name1','newaddress1','newtelephone1','newstationidName1',
'newdealername1','dealername1','buno1','transstockdate1','newproductname1','newaccountidName1',
'newtype1','newdeposit1','returned1','newpresaledealername1','underlinestr1','productname1',
'productdealernumber1','depositdealer1','depositdealernumber1','isonline1','transdate1',
'serno1','productcode1','accountincustcode1','accountincustustname1','accountoutcustcode1',
'accountoutcustname1','transstatus1','dealercustcode1','dealercustname1','distributorcustcode1',
'distributorcustname1','type1','state1','statuscode1','installDate1']

print('=== 从 Subreport1(装箱单) 提取的字段 ===')
patterns_sr1 = ['SHIPSTATUS1','zxd1','shipdate1','newerpshipno1','ProductNumber1',
                 'newproductidName1','newordsalesorderidName1','newname1','newproductname1',
                 'newtype1','newdeposit1','newaccountidName1','newpayaccountidName1']

for fid in patterns_sr1:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', content, re.DOTALL)
    if m:
        print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')

print('\n=== 从 Subreport2(服务单) 提取的字段 ===')
patterns_sr2 = ['servno1','typestr1','newproductidName1','newproductname1',
                 'newdealername1','newstationidName1','newpresaledealername1',
                 'name1','newaddress1','newtelephone1','statustr1','underlinestr1','newisclosed1']
for fid in patterns_sr2:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', content, re.DOTALL)
    if m:
        print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')

print('\n=== 从 Subreport3(保卡扫描) 提取的字段 ===')
# Need to find field IDs for 保卡扫描
sr3_start = content.find('id="Subreport3"')
sr3_end = content.find('id="Subreport4"')
sr3_body = content[sr3_start:sr3_end]
field_ids_in_sr3 = re.findall(r'id="([a-zA-Z0-9]+1)"', sr3_body)
unique_ids = list(dict.fromkeys(field_ids_in_sr3))
print('  可能的字段ID:', unique_ids)

print('\n=== 从 Subreport4(押金返还) 提取的字段 ===')
sr4_start = content.find('id="Subreport4"')
sr4_end = content.find('id="Subreport5"')
sr4_body = content[sr4_start:sr4_end]
field_ids_in_sr4 = re.findall(r'id="([a-zA-Z0-9]+1)"', sr4_body)
unique_ids4 = list(dict.fromkeys(field_ids_in_sr4))
print('  可能的字段ID:', unique_ids4)
for fid in unique_ids4:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr4_body, re.DOTALL)
    if m:
        print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')

print('\n=== 从 Subreport5(设备档案) 提取的字段 ===')
patterns_sr5 = ['newname1','newproductname1','productname1','dealername1',
                 'productdealernumber1','name1','newtelephone1','instlled1',
                 'returned1','newdeposit1','isonline1','depositdealer1',
                 'depositdealernumber1','installDate1','shipdate1']
for fid in patterns_sr5:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', content, re.DOTALL)
    if m:
        print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')

print('\n=== 从 Subreport6(库存调整单) 提取的字段 ===')
sr6_start = content.find('id="Subreport6"')
sr6_end = content.find('id="Subreport7"')
sr6_body = content[sr6_start:sr6_end]
field_ids_in_sr6 = re.findall(r'id="([a-zA-Z0-9]+1)"', sr6_body)
unique_ids6 = list(dict.fromkeys(field_ids_in_sr6))
print('  可能的字段ID:', unique_ids6)
for fid in unique_ids6:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr6_body, re.DOTALL)
    if m:
        print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')

print('\n=== 从 Subreport7(调拨单) 提取的字段 ===')
patterns_sr7 = ['buno1','transdate1','serno1','productcode1','productname1',
                 'accountincustcode1','accountincustustname1','accountoutcustcode1',
                 'accountoutcustname1','transstatus1']
for fid in patterns_sr7:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', content, re.DOTALL)
    if m:
        print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')

print('\n=== 从 Subreport8(移库单) 提取的字段 ===')
patterns_sr8 = ['buno1','transdate1','serno1','productcode1','productname1',
                 'dealercustcode1','dealercustname1','distributorcustcode1',
                 'distributorcustname1','type1','state1']
for fid in patterns_sr8:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', content, re.DOTALL)
    if m:
        print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')

print('\n=== 从 Subreport9(移机单) 提取的字段 ===')
sr9_start = content.find('id="Subreport9"')
sr9_end = content.find('id="Subreport10"')
sr9_body = content[sr9_start:sr9_end]
field_ids_in_sr9 = re.findall(r'id="([a-zA-Z0-9]+1)"', sr9_body)
unique_ids9 = list(dict.fromkeys(field_ids_in_sr9))
print('  可能的字段ID:', unique_ids9)
for fid in unique_ids9:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', sr9_body, re.DOTALL)
    if m:
        print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')

print('\n=== 从 Subreport10(库存条码明细) 提取的字段 ===')
patterns_sr10 = ['newname1','newproductname1','newproductidName1','dealername1',
                  'newstatus1','statuscode1','newdeposit1']
for fid in patterns_sr10:
    m = re.search(rf'id="{fid}"[^>]*>.*?<span[^>]*>([^<]+)</span>', content, re.DOTALL)
    if m:
        print(f'  {fid}: {m.group(1).replace(chr(160)," ").strip()}')
