import urllib.request, json, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request('http://localhost:5001/api/barcodes', headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req, context=ctx)
data = json.loads(resp.read())

barcodes = data.get('barcodes', [])
for b in barcodes[:3]:
    f = b['fields']
    print(f"\nBarcode: {b['barcode']}")
    print(f"  sr keys: {list(f.keys())}")
    sr1 = f.get('sr1', {})
    if isinstance(sr1, dict):
        print(f"  sr1 keys: {list(sr1.keys())}")
        print(f"  newproductidName1_sr1 value: {sr1.get('newproductidName1', 'NOT FOUND')}")
    else:
        print(f"  sr1: {sr1}")

# Test _get_field logic
import re
def _get_field(fields, field_id):
    m = re.search(r'_sr(\d+)$', field_id)
    if not m:
        return fields.get(field_id, '')
    sr_num = m.group(1)
    sr_key = f'sr{sr_num}'
    real_fid = field_id.rsplit('_sr', 1)[0]
    sub = fields.get(sr_key, {})
    if isinstance(sub, list) and sub:
        sub = sub[0]
    if isinstance(sub, dict):
        return sub.get(real_fid, '')
    return ''

# Test with first barcode
f0 = barcodes[0]['fields']
print(f"\n_get_field test for newproductidName1_sr1: {_get_field(f0, 'newproductidName1_sr1')}")
print(f"_get_field test for dealername1_sr10: {_get_field(f0, 'dealername1_sr10')}")
