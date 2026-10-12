"""Activate a verified comparison catalog in the already registered static asset."""
import argparse
import hashlib
import json
from pathlib import Path
from build_refined_baseline import START, END, canonical
from run_baseline_refinement import digest

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--catalog",required=True)
    parser.add_argument("--js",required=True)
    args=parser.parse_args();catalog_path=Path(args.catalog);js=Path(args.js)
    receipt_path=catalog_path.with_name("PUBLICATION_RECEIPT.json")
    if receipt_path.exists():raise ValueError("version already published")
    receipt=json.loads(catalog_path.with_name("BUILD_RECEIPT.json").read_bytes())
    if digest(catalog_path)!=receipt["catalog_sha256"] or digest(catalog_path.with_name("bundle.json"))!=receipt["bundle_sha256"]:
        raise ValueError("prepared artifact hash mismatch")
    catalog=json.loads(catalog_path.read_bytes())
    if catalog["active_variant"]!="refined_baseline" or len(catalog["rows"])!=10:raise ValueError("unexpected comparison catalog")
    pointer_names=("giving-models-current.json","pac-giving-current.json")
    pointers={str(js.parent/"data"/n):digest(js.parent/"data"/n) for n in pointer_names}
    prior=js.read_text(encoding="utf-8");oldhash=digest(js)
    if prior.count(START)!=1 or prior.count(END)!=1:raise ValueError("exactly one generated block required")
    head,rest=prior.split(START);_,tail=rest.split(END)
    block=START+"\nwindow.MENTAT_PARTISAN_POLITICAL = "+json.dumps(catalog,sort_keys=True,allow_nan=False)+";\nwindow.dispatchEvent(new Event('mentat-political-forecast-ready'));\n"+END
    updated=head+block+tail
    temp=js.with_name(js.name+".baseline-publish.tmp")
    temp.write_text(updated,encoding="utf-8")
    if digest(js)!=oldhash:raise ValueError("static asset changed while preparing publish")
    temp.replace(js)
    if any(digest(p)!=sha for p,sha in pointers.items()):raise ValueError("unrelated published pointer changed")
    published=dict(receipt,published=True,js_path=str(js),js_before_sha256=oldhash,js_sha256=digest(js),
                   unchanged_model_pointers=pointers,publisher_sha256=digest(__file__))
    receipt_path.write_bytes(canonical(published))
    print(json.dumps(published,indent=2))

if __name__=="__main__":main()
