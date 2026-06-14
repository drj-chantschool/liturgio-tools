import re
from sqlalchemy import create_engine, text
from unidecode import unidecode
import time
import keyring

import functools
def compose2(f, g):
    return lambda *a, **kw: f(g(*a, **kw))
def compose(*fs):
    return functools.reduce(compose2, fs)

from liturgio_tools.gregobase import load_chants

whereclause = ''

chants = load_chants( f'select * from gregobase_chants {whereclause}' , error='warn')

def get_engine(user):
    service = 'liturgio-mysql'
    password = keyring.get_password(service, user)
    if password is None:
        sys.exit(f'ERROR: No keyring password for {user}. '
                 f'Set it with: python -c "import keyring; keyring.set_password(\'{service}\', \'{user}\', \'PASSWORD\')"')
    return create_engine(f'mysql+mysqlconnector://{user}:{password}@localhost:3306/liturgio', echo=False)

ro_engine = get_engine('liturgio_ro')
with ro_engine.connect() as connection:
    res = connection.execute( text( f'select count(*) cnt from gregobase_chants {whereclause}'))
    for row in res.mappings():
        nchant = row['cnt']

# ── text_decode filters (ASCII-normalized, lowercased) ───────────────────────
def strip_colons( mystr ):
    return re.sub(r':','',mystr)

def strip_html( mystr ):
    return re.sub(r'<[^>]*>', '', mystr )

def strip_braces( mystr ):
    return re.sub(r'{|}','',mystr)

def strip_specs( mystr ):
    return re.sub(r'((R|V)/\s*\.?)|\*|(E u o u a e\s*\.?)|~|i\s*j\s*\.?|(\\greheightstar)|\'','',mystr)

def fix_spaces(mystr):
    return re.sub(r'^\s*','', re.sub('\s+',' ', mystr))

def fix_caps(mystr : str):
    return ' '.join( word[0] + word[1:].lower() for word in mystr.split(' ') if len(word) )

decode_filter = compose( unidecode, fix_spaces, fix_caps, strip_specs, strip_braces, strip_html, strip_colons )

# ── text filters (preserves accents and case) ────────────────────────────────
def strip_liturgical_markers(mystr):
    # Remove: * V/ R/ ij E u o u a e (euouae), \greheightstar, ~ '
    return re.sub(r'((R|V)/\s*\.?)|\*|(E\s+u\s+o\s+u\s+a\s+e\s*\.?)|~|i\s*j\s*\.?|(\\greheightstar)|\'', '', mystr)

text_filter = compose( fix_spaces, strip_liturgical_markers, strip_braces, strip_html, strip_colons )

rw_engine = get_engine('jcost')
failedtoparse = 0
failedtopush = 0

stmt = text("""
INSERT INTO gregobase_chants_texts (id, text, text_decode)
VALUES (:id, :text, :txt)
ON DUPLICATE KEY UPDATE text = :text, text_decode = :txt
""")

start = time.time()
BATCH = 10
processed = 0

it = iter(chants)
batch = []

def flush(connection, batch):
    for params in batch:
        connection.execute(stmt, params)
    connection.commit()

while True:
    try:
        chant = next(it)
    except StopIteration:
        break
    except Exception as e:
        print(f"Error generating chant: {e}")
        failedtoparse += 1
        continue

    try:
        raw = chant.text
        txt_decode = decode_filter(raw)
        txt_text   = text_filter(raw)
        batch.append({"id": chant.id, "text": txt_text, "txt": txt_decode})
        processed += 1

    except Exception as e:
        print(f"Error processing chant {getattr(chant,'id','?')}: {e}")
        failedtopush += 1
        continue

    if processed % BATCH == 0:
        with rw_engine.begin() as connection:
            flush(connection, batch)
        batch = []
        elapsed = time.time() - start
        rate = processed / elapsed if elapsed > 0 else 0.0
        remaining = nchant - processed
        eta_seconds = remaining / rate if rate > 0 else float("inf")
        print(
            f"{processed}/{nchant} processed | "
            f"{rate:.2f} chants/s | elapsed {elapsed/60:.1f} min | "
            f"ETA {eta_seconds/60:.1f} min"
        )

# flush remaining
if batch:
    with rw_engine.begin() as connection:
        flush(connection, batch)


elapsed = time.time() - start
rate = processed / elapsed if elapsed else 0

print("\n========== FINAL REPORT ==========")
print(f"Total chants:     {nchant}")
print(f"Processed:        {processed}")
print(f"Failed to parse:  {failedtoparse}")
print(f"Failed to push:   {failedtopush}")
print(f"Elapsed:          {elapsed/60:.1f} minutes")
print(f"Avg speed:        {rate:.2f} chants/sec")
print("==================================")
