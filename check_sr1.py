import re, html as html_mod

with open('/Users/gongqi/Desktop/CRM/barcode_query 3/barcode/7132408080810.html') as f:
    content = f.read()

sr1_start = content.find('id="Subreport1"')
sr1_end = content.find('id="Subreport2"')
sr1_body = content[sr1_start:sr1_end]

# 精确提取 sr1 内每个字段 div 的值
# 每个字段 div 结构: <div id="FIELDID" ... > ... <span>VALUE</span> ...
print('=== Subreport1 内所有字段 ===')
for m in re.finditer(r'<div id="([a-zA-Z0-9]+1)"', sr1_body):
    fid = m.group(1)
    # 找这个 div 的结束位置（下一个同级 div 或 </div>）
    div_start = m.start() + len(m.group(0))
    div_end_search = sr1_body[m.start():]
    # 找到 </div> 闭合
    close_m = re.search(r'</div>', div_end_search)
    if close_m:
        div_content = div_end_search[:close_m.start()]
        # 找 span 里的值
        span_m = re.search(r'<span[^>]*>([^<]+)</span>', div_content)
        if span_m:
            val = html_mod.unescape(span_m.group(1)).replace('\xa0', ' ').strip()
            if val and val not in ['¥.00', '.00', '', ' ']:
                print(f'  {fid}: [{val}]')
