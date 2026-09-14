"""Compile content hashes into existing static and lazy-loading entrypoints."""
import argparse
import hashlib
import json
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def compile_assets(check=False):
    boot=ROOT/'web_demo/bootstrap.js';source=boot.read_text()
    files=re.findall(r'\["[^"]+", "[^"]+", "[^"]+", "([^"]+\.js)"\]',source)
    hashes={f:hashlib.sha256((ROOT/'web_demo/modules'/f).read_bytes()).hexdigest() for f in files}
    updated=re.sub(r'  const ASSET_HASHES = .*?;', '  const ASSET_HASHES = '+json.dumps(hashes,separators=(',',':'))+';',source)
    index=ROOT/'web_demo/index.html';original=index.read_text()
    def replace(match):
        path=ROOT/'web_demo'/match[2]
        if not path.is_file():return match[0]
        data=updated.encode() if path==boot else path.read_bytes()
        return match[1]+'?v='+hashlib.sha256(data).hexdigest()+'"'
    html=re.sub(r'((?:src|href)="/web_demo/([^"?]+))(?:\?[^" ]*)?"',replace,original)
    if check:
        if source!=updated or original!=html:raise ValueError('FRONTEND_ASSET_HASH_STALE')
    else:
        boot.write_text(updated);index.write_text(html)
    return {'verified':True,'lazyModuleCount':len(hashes)}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true')
    print(json.dumps(compile_assets(parser.parse_args().check)))
