"""
Load chant rows from the ``liturgio`` database's ``gregobase_chants`` table
as :class:`gabc_tools.gbchant.GBChant` objects.

This is the database-coupled half of the GBChant split: gabc-tools provides
pure GABC parsing/analysis, this module provides the ``liturgio_ro``
connection and yields ``GBChant`` instances built from query rows.
"""
from typing import Iterator

from sqlalchemy import create_engine, text

from gabc_tools.gbchant import GBChant
from gabc_tools.utils import strcmp


def load_chants(myquery: str, error: str = 'ask') -> Iterator[GBChant]:
    connection_string = "mysql+mysqlconnector://liturgio_ro:liturgio_ro@localhost:3306/liturgio"
    engine = create_engine(connection_string, echo=True)
    failed = {}
    with engine.connect() as connection:
        res = connection.execute(text(myquery))
        for row in res.mappings():
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
            except:
                print(row)
                raise
