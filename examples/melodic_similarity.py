"""
Example: load chants from the liturgio database, run the gabc-tools
MelodicPipeline, and produce a Bokeh visualization of melodic similarity.
"""
from gabc_tools.pipeline import MelodicPipeline
from liturgio_tools.gregobase import load_chants

# === Configuration ===
OFFICE_PART = 'gr'         # office-part to query: rb, gr, an, in, of, co, etc.
VERSION = '%Solesmes%'
VOCAB_METHOD = 'freq'       # 'bpe' or 'freq'
CLUSTER_METHOD = 'hdbscan'  # 'agglomerative' or 'hdbscan'
N_CLUSTERS = None           # None = auto-select via silhouette score
ALIGNMENT_TOP_K = 50        # number of cosine neighbors to refine with NW alignment

# === Load chants ===
query = (
    f"select * from gregobase_chants "
    f"where `office-part`='{OFFICE_PART}' "
    f"and version like '{VERSION}'"
)
chants = list(load_chants(query, error='warn'))
print(f"Loaded {len(chants)} chants")

# === Run pipeline ===
pipeline = MelodicPipeline(OFFICE_PART, version=VERSION, vocab_method=VOCAB_METHOD)
result = pipeline.run(
    chants,
    n_clusters=N_CLUSTERS,
    cluster_method=CLUSTER_METHOD,
    alignment_top_k=ALIGNMENT_TOP_K,
)

# === Print top melodic words ===
print("\nTop melodic words in vocabulary:")
for tid in range(min(20, result.vocab.size)):
    print(f"  {tid}: {result.vocab.token_str(tid)}")

# === Bokeh visualization ===
from bokeh.io import output_file, show
from bokeh.models import ColumnDataSource, HoverTool, LinearColorMapper, TapTool, OpenURL
from bokeh.palettes import brewer
from bokeh.plotting import figure
from bokeh.transform import transform

list_x = result.tsne_coords[:, 0]
list_y = result.tsne_coords[:, 1]
desc = [ch.incipit for ch in result.chants]
modes = [int(ch.mode or 0) if (ch.mode is None) or (ch.mode.isnumeric()) else 9 for ch in result.chants]
clusters = result.cluster_labels.tolist()
urls = [f'https://gregobase.selapa.net/chant.php?id={ch.id}' for ch in result.chants]

# Build neume display strings from syllable data
neume_displays = []
for syls in result.syllables:
    parts = []
    for s in syls:
        if s.is_boundary:
            parts.append(s.neume_raw + '<br>')
        elif s.pitches:
            parts.append(''.join(s.pitches))
    neume_displays.append(' '.join(parts))

source = ColumnDataSource(data=dict(
    x=list_x, y=list_y, desc=desc, mode=modes, cluster=clusters,
    url=urls, text=neume_displays,
))

hover = HoverTool()
hover.tooltips = """
Index: $index<br>
Incipit: @desc<br>
Mode: @mode<br>
Cluster: @cluster<br>
Neumes: @text
"""
taptool = TapTool(callback=OpenURL(url='@url'))

n_clusters_actual = len(set(clusters) - {-1})
palette_size = max(3, min(n_clusters_actual + 1, 12))
mapper = LinearColorMapper(palette=brewer['Paired'][palette_size], low=min(clusters), high=max(clusters))

p = figure(min_width=800, min_height=800, tools=[hover, taptool],
           title=f"{OFFICE_PART} chants — {VOCAB_METHOD} vocab, {CLUSTER_METHOD} clustering")
p.scatter('x', 'y', size=8, source=source,
          fill_color=transform('cluster', mapper), legend_group='cluster')
p.legend.location = "bottom_left"

output_file(f'{OFFICE_PART}_melodic_similarity.html')
show(p)
