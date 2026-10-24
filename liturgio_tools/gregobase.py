"""
Load chant rows from the ``liturgio`` database's ``chants`` table
as :class:`gabc_tools.gbchant.GBChant` objects.

The query should select ``elements`` (the first verse becomes the chant's
``gabc``); a row that has a ``gabc`` column instead is used as is.

This is the database-coupled half of the GBChant split: gabc-tools provides
pure GABC parsing/analysis, this module provides the ``liturgio_ro``
connection and yields ``GBChant`` instances built from query rows.
"""
from typing import Iterator

from sqlalchemy import create_engine, text

from gabc_tools.elements import elements_gabc_body, load_elements
from gabc_tools.gbchant import GBChant
from gabc_tools.utils import strcmp


def load_chants(myquery: str, error: str = 'ask') -> Iterator[GBChant]:
    connection_string = "mysql+mysqlconnector://liturgio_ro:liturgio_ro@localhost:3306/liturgio"
    engine = create_engine(connection_string, echo=True)
    failed = {}
    with engine.connect() as connection:
        res = connection.execute(text(myquery))
        for row in res.mappings():
            if 'elements' in row:
                row = dict(row, gabc=elements_gabc_body(load_elements(row['elements']), first_verse=True))
            try:
                gbch = GBChant(row)
                yield gbch
            except EncodingWarning as ex:
                if error in ('ask', 'fail', 'warn'):
                    print('***** ENCODING FAILED *****')
                    failed[row['incipit']] = row
                    print('Given:       ', ex.args[1])
                    print('Interpreted: ', ex.args[2])
                    strcmp(ex.args[1], ex.args[2])
                    print(row)
                if error == 'ask':
                    x = input('Keep anyway? (N/n)o/anything else: ')
                    if x.lower().startswith('n'):
                        raise
                elif error == 'fail':
                    raise
            except Exception as e:
                print(row)
                print(f'***** SKIPPED (id={row.get("id")}): {e} *****')
                continue
