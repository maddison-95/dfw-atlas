"""Assemble the MapLibre version: template3.html + data/areas_meta.json -> index.html (production)
and test_index.html (offline test). Run this FROM INSIDE site/ (the folder this file lives in) --
paths below are relative to that. Re-run any time data/areas_meta.json changes (by hand, or after
scripts/build_places.py adds new cities) so index.html picks up the change -- index.html embeds that
file's contents directly at build time, it does not fetch it at runtime.
"""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
meta = json.load(open(os.path.join(HERE, 'data', 'areas_meta.json')))
tpl = open(os.path.join(HERE, 'template3.html')).read()
PROD = {
    'css': 'https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css',
    'js': 'https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js',
    'style': json.dumps('https://tiles.openfreemap.org/styles/positron'),
}
TEST = {
    'css': 'vendor/maplibre-gl.css',
    'js': 'vendor/maplibre-gl.js',
    'style': json.dumps({"version": 8, "glyphs": "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
                         "sources": {"empty": {"type": "geojson", "data": {"type": "FeatureCollection", "features": []}}},
                         "layers": [{"id": "bg", "type": "background", "paint": {"background-color": "#EDEAE3"}},
                                    {"id": "dummy-symbol", "type": "symbol", "source": "empty"}]}),
}

def build(cfg):
    return (tpl.replace('%%DATA%%', json.dumps(meta, separators=(',', ':')))
               .replace('%%MAPLIBRE_CSS%%', cfg['css']).replace('%%MAPLIBRE_JS%%', cfg['js']).replace('%%STYLE%%', cfg['style']))

open(os.path.join(HERE, 'index.html'), 'w').write(build(PROD))
open(os.path.join(HERE, 'test_index.html'), 'w').write(build(TEST).replace('<script>\nconst META', '<script>window.__NO_TEXT__=true;</script>\n<script>\nconst META'))
print('index.html', os.path.getsize(os.path.join(HERE, 'index.html')) // 1024, 'KB')
