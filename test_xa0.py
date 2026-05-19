import json

with open('/tmp/bc_test.json') as f:
    bc_data = json.load(f)
with open('/tmp/filter_opts.json') as f:
    opt_data = json.load(f)

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
service_field = 'newdealername1_sr2'

# Get filter options
filter_opts = {}
for opt in opt_data.get('filters', []):
    filter_opts[opt['field_id']] = opt['options']

# Check for \xa0 mismatches
print("=== Checking for \\xa0 mismatches ===")
for b in bc_data['barcodes']:
    for fid in [filter_field, status_field, dealer_field, service_field]:
        val = gf_fields(b['fields'], fid)
        if val and '\xa0' in val:
            # Check if there's a matching option without \xa0
            normalized = val.replace('\xa0', ' ')
            if normalized.strip() != val.strip():
                print("  {}: {} -> normalized: {}".format(
                    b['barcode'], repr(val), repr(normalized)))
                opts = filter_opts.get(fid, [])
                if normalized in opts:
                    print("    MATCH FOUND: normalized version IS in filter options!")
                elif val not in opts:
                    print("    PROBLEM: val NOT in options, normalized NOT in options")
                    print("    Options sample: {}".format([repr(o) for o in opts[:3]]))

print("\n=== Check filter options contain raw extracted values ===")
for fid in [filter_field, status_field, dealer_field, service_field]:
    opts = set(filter_opts.get(fid, []))
    mismatches = 0
    for b in bc_data['barcodes']:
        val = gf_fields(b['fields'], fid)
        if val and val not in opts:
            mismatches += 1
    if mismatches > 0:
        print("  {}: {} barcodes have gf value NOT in filter options".format(fid, mismatches))
        for b in bc_data['barcodes'][:3]:
            val = gf_fields(b['fields'], fid)
            if val and val not in opts:
                print("    {}: {!r} not in options".format(b['barcode'], val))
    else:
        print("  {}: ALL match (OK)".format(fid))
