import json

with open('/tmp/bc_test.json') as f:
    data = json.load(f)

def gf_fields(fields, field_id):
    idx = field_id.rfind('_sr')
    if idx == -1:
        return fields.get(field_id, '')
    sr_num = field_id[idx+3:]
    sr_key = 'sr' + sr_num
    real_key = field_id[:idx]
    sub = fields.get(sr_key, {})
    if isinstance(sub, list):
        return ''
    if isinstance(sub, dict):
        return sub.get(real_key, '')
    return ''

filter_field = 'newproductidName1_sr2'
status_field = 'newisclosed1_sr2'
dealer_field = 'dealername1_sr10'

print("=== Filter field:", filter_field, "===")
for b in data['barcodes'][:5]:
    val = gf_fields(b['fields'], filter_field)
    print("  {}: {}".format(b['barcode'], repr(val)))

print("\n=== Status field:", status_field, "===")
for b in data['barcodes'][:5]:
    val = gf_fields(b['fields'], status_field)
    print("  {}: {}".format(b['barcode'], repr(val)))

print("\n=== Filter matching test (selected=['已结单']) ===")
selected = ['已结单']
for b in data['barcodes'][:5]:
    val = gf_fields(b['fields'], status_field)
    matched = val in selected
    print("  {}: val={}, matched={}".format(b['barcode'], repr(val), matched))

print("\nTotal barcodes in API:", data['total'])
print("Barcodes returned:", len(data['barcodes']))
