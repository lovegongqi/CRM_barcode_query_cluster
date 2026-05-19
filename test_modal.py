import urllib.request, json, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request('http://localhost:5001/api/barcodes/7162411250297', headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req, context=ctx)
d = json.loads(resp.read())
f = d['fields']

# Simulate gf() and section() logic in Python
def gf(f, key):
    idx = key.rfind('_sr')
    if idx == -1:
        return f.get(key, '')
    srKey = key[idx+1:]
    realKey = key[:idx]
    sub = f.get(srKey, {})
    if isinstance(sub, list):
        return ''
    if sub and isinstance(sub, dict):
        return sub.get(realKey, '')
    return ''

def rpt(label, key):
    value = gf(f, key)
    if not value:
        return ''
    return f'<div class="rpt-row"><span class="rpt-label">{label}</span><span class="rpt-value">{value}</span></div>'

def section(num, title, primaryKey, entries):
    srKey = 'sr' + str(num)
    sub = f.get(srKey, {})
    if not sub:
        return ''
    if isinstance(sub, list):
        if len(sub) == 0:
            return ''
        return '\n'.join([
            f'<div class="rpt-section"><div class="rpt-section-title">{title} - {rec.get(primaryKey, f"第{i+1}条")}</div><div class="rpt-section-body">'
            + ''.join([rpt(label, key) for label, key in entries if rec.get(key, '')])
            + '</div></div>'
            for i, rec in enumerate(sub)
        ])
    else:
        rows = ''.join([rpt(label, key) for label, key in entries if sub.get(key, '')])
        return f'<div class="rpt-section"><div class="rpt-section-title">{title}</div><div class="rpt-section-body">{rows}</div></div>'

html = section(5, '设备档案', 'myproductdealer1', [
    ['条码', 'newname1'], ['物料编码', 'newproductname1'], ['物料描述', 'productname1'],
]) + section(2, '服务单', 'servno1', [
    ['服务单号', 'servno1'], ['物料描述', 'newproductidName1'], ['状态', 'statustr1'],
    ['是否结单', 'newisclosed1'],
]) + section(1, '装箱单', 'zxd1', [
    ['条码', 'newname1'], ['装箱单号', 'zxd1'], ['物料名称', 'newproductname1'],
])

print(f'Generated HTML length: {len(html)}')
print('Sample:', html[:500] if html else 'EMPTY!')
