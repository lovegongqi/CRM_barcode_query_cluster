import json

with open('/tmp/filter_opts.json') as f:
    data = json.load(f)

filter_field = 'newproductidName1_sr2'
status_field = 'newisclosed1_sr2'
dealer_field = 'dealername1_sr10'
service_field = 'newdealername1_sr2'

for opt in data.get('filters', []):
    fid = opt['field_id']
    if fid in [filter_field, status_field, dealer_field, service_field]:
        print("Field: {} ({})".format(fid, opt['label']))
        for v in opt['options']:
            print("  opt: {}".format(repr(v)))
        print()

with open('/tmp/bc_test.json') as f:
    bc_data = json.load(f)

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

print("=== Simulating filter by '已结单' ===")
selected_status = ['已结单']
matched = 0
for b in bc_data['barcodes']:
    val = gf_fields(b['fields'], status_field)
    if val in selected_status:
        matched += 1
        print("  MATCH: {} -> {}".format(b['barcode'], repr(val)))
print("Total matched: {} / {}".format(matched, bc_data['total']))

print("\n=== Check: is gf_fields returning empty strings? ===")
empty_count = 0
for b in bc_data['barcodes']:
    for fid in [filter_field, status_field, dealer_field, service_field]:
        val = gf_fields(b['fields'], fid)
        if val == '':
            empty_count += 1
print("Empty gf_fields calls: {} total".format(empty_count))
