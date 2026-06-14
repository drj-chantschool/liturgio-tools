from sqlalchemy import create_engine, text
from getpass import getpass
import re
connection_string = f"mysql+mysqlconnector://jcost:{getpass('Password:')}@localhost:3306/liturgio"
engine = create_engine(connection_string, echo=True)

with engine.connect() as connection:
    connection.execute(text('create table if not exists psalms (chapter int, verse int, text text)'))
    with open('psalms-vul.txt') as f:
        for line in f:
            #print(line.strip())
            match = re.match('<a name="([0-9]+):([0-9]+)"> (.*)', line.strip())
            #print(match.group(1), match.group(2), match.group(3))
            connection.execute(text('insert into psalms (chapter, verse, text) values (:1,:2,:3)'),{'1':match.group(1), '2':match.group(2), '3':match.group(3)} )

    connection.commit()