import re, json

with open('gregobase_online.sql', encoding='utf-8') as f:
    sql = f.read()

# Pull the 889 chants row
idx = sql.index('(889, NULL')
row_end = sql.index('\n(889, 2,', idx)
row = sql[idx:row_end].rstrip(',\n')

# Extract: (id, cantusid, version, incipit, initial, office-part, mode, mode_var, transcriber, commentary, headers, gabc, gabc_verses, tex_verses, remarks, copyrighted, duplicateof)
# Simple approach: strip outer parens and split on top-level commas (respecting quoted strings)
inner = row[1:-1]  # remove outer ( )

fields = []
buf = []
in_str = False
esc = False
for ch in inner:
    if esc:
        buf.append(ch)
        esc = False
    elif ch == '\\':
        buf.append(ch)
        esc = True
    elif ch == "'" and not in_str:
        in_str = True
        buf.append(ch)
    elif ch == "'" and in_str:
        in_str = False
        buf.append(ch)
    elif ch == ',' and not in_str:
        fields.append(''.join(buf).strip())
        buf = []
    else:
        buf.append(ch)
fields.append(''.join(buf).strip())

col_names = ['id','cantusid','version','incipit','initial','office-part','mode','mode_var',
             'transcriber','commentary','headers','gabc','gabc_verses','tex_verses','remarks',
             'copyrighted','duplicateof']

row_dict = {}
for name, val in zip(col_names, fields):
    if val == 'NULL':
        row_dict[name] = None
    elif val.startswith("'") and val.endswith("'"):
        # SQL string: unescape \' and \\
        s = val[1:-1]
        s = s.replace("\\'", "'").replace("\\\\", "\\")
        row_dict[name] = s
    else:
        try:
            row_dict[name] = int(val)
        except ValueError:
            row_dict[name] = val

print('id:', row_dict['id'])
print('cantusid:', row_dict['cantusid'])
print('version:', row_dict['version'])
print('incipit:', row_dict['incipit'])
print('initial:', row_dict['initial'])
print('office-part:', row_dict['office-part'])
print('mode:', row_dict['mode'])
print('mode_var:', row_dict['mode_var'])
print('transcriber:', row_dict['transcriber'])
print('commentary:', repr(row_dict['commentary']))
print('headers:', row_dict['headers'])
print('gabc (first 100):', repr(row_dict['gabc'][:100]) if row_dict['gabc'] else None)
print('gabc_verses:', row_dict['gabc_verses'])
print('tex_verses:', row_dict['tex_verses'])
print('remarks:', row_dict['remarks'])
print('copyrighted:', row_dict['copyrighted'])
print('duplicateof:', row_dict['duplicateof'])

# Parse gabc JSON to extract the raw gabc body
gabc_json = json.loads(row_dict['gabc'])
for entry in gabc_json:
    if entry[0] == 'gabc':
        print()
        print('gabc body (first 100):', repr(entry[1][:100]))
