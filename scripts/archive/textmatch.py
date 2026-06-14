from sqlalchemy import create_engine #, text
from getpass import getpass
import re, sys
from collections import defaultdict
from math import sqrt
import pandas as pd
from gabc_tools.gbchant import GBChant
import numpy as np
from unidecode import unidecode

mytext = ' '.join(sys.argv[1:])
ntok_max=6

def nvectorize( mystr:str, n_tok:int ):
    this_dict = defaultdict(int)
    for tok in (lambda y: [ ' '.join(y[x:x+n_tok]) for x in range( len(y) -n_tok+1) ])(mystr.split()):
        this_dict[tok] += 1
    return this_dict

def textfilter(s:str):
    return ''.join([i for i in unidecode(s) if i.isalpha() or i.isspace() ])

# dot product of dicts
def dict_prod(dict1:dict, dict2:dict):
    return sum( y*dict2.get(x,0) for x,y in dict1.items() )

def dict_norm( dict1 ):
    return sqrt(dict_prod( dict1, dict1 ))

def text_simil( query_s:str, corpus_s:str, ntok:int, normalize=False):
    query_d = nvectorize( query_s.lower(), ntok)
    corpus_d = nvectorize( corpus_s.lower(), ntok)
    return dict_prod(query_d,corpus_d)/( ( sum(y for _,y in query_d.items() ) or 1) if normalize else 1 )

sim_vec = lambda ntok: np.vectorize(lambda x,y: text_simil(x,y,ntok,True))

connection_string = f"mysql+mysqlconnector://liturgio_ro:liturgio_ro@localhost:3306/liturgio"
engine = create_engine(connection_string, echo=True)

psalms_df = pd.read_sql( 'select chapter,verse,text from vulgclementine_verses vv inner join vulgclementine_books vb on vv.book_id = vb.id where vb.name = \'Psalms\'', engine,index_col=['chapter','verse'])
graduals_df = pd.read_sql( 'select * from gregobase_chants where `office-part`=\'gr\' and version=\'Solesmes\' limit 10', engine, index_col=['id','incipit'])
graduals_df['text'] = graduals_df['gabc'].apply( lambda x: GBChant({'gabc':x}).text )

print(graduals_df)
print(psalms_df.loc[36,3])

psalms_df['text']=psalms_df['text'].apply(textfilter)
graduals_df['text']=graduals_df['text'].apply(textfilter)

print( psalms_df['text'] )
print( graduals_df['text'] )

scores_df = pd.concat( {
       ii: psalms_df['text'].apply( lambda ps: graduals_df['text'].apply( lambda gr: sim_vec(ii)(gr, ps)) )
       for ii in range(1,ntok_max+1)
       }
       , axis=1
       )
scores_df.rename_axis(['ngram','id','incipit'], axis='columns', inplace=True)

#scores_df = (scores_df/scores_df.agg('sum')).fillna(0)

print(scores_df)

print(scores_df.agg(['max','argmax','idxmax']).sort_values( by=['ngram','max'],axis=1 ).T )


exit()

#print( psalms_df.corrwith( graduals_df['text'], axis=1, method=lambda x,y: text_simil(x,y,1) ))

for idx, row in graduals_df.iterrows():
    print(idx, row)
    mytext = row['text']


    for ii in range(1,ntok_max+1):
        psalms_df[f'n{ii}_sim'] = psalms_df['text'].apply( lambda x: sim_vec(ii)(x, mytext) )
        psalms_df[f'n{ii}_sim'] = psalms_df[f'n{ii}_sim']/(psalms_df[f'n{ii}_sim'].sum() or 1)

    print(psalms_df.sort_values( by=[f'n{ii}_sim' for ii in range(ntok_max,0,-1)]) ) #.melt(id_vars=['chapter','verse'],value_vars=['n1_sim','n2_sim','n3_sim'],var_name='sim_meas',value_name='score'))
    print( psalms_df.groupby(by=['chapter']).sum().sort_values( by=[f'n{ii}_sim' for ii in range(ntok_max,0,-1)]))
