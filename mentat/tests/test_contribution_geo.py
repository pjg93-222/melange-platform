"""Geographic IDs, provenance, completeness, cache safety and money separation."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "app"
spec = importlib.util.spec_from_file_location("contribution_geo", APP / "contribution_geo.py")
geo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geo)


def collection(zips):
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"GEOID": z, "NAME": "ZCTA " + z},
        "geometry": {"type": "Polygon", "coordinates": [[[-71,42],[-70,42],[-70,43],[-71,42]]]}} for z in zips]}


class ContributionGeoTests(unittest.TestCase):
    def gazetteer(self):
        return {"data": {"02134": [-71.13,42.36], "00601": [-66.75,18.18], "99501": [-149.88,61.2], "96813": [-157.85,21.32]}, "sha256": "a"*64, "fetched_at": "2026-09-12"}

    def test_leading_zero_zip_and_unknown_are_preserved(self):
        with patch.object(geo, "_gazetteer", self.gazetteer):
            result = geo.geography({"zips": ["02134","00601","99999","", "02134"], "layers": []})
        self.assertEqual([p["zip5"] for p in result["points"]], ["00601", "02134"])
        self.assertEqual(result["coverage"]["requested_zips"], 3)
        self.assertEqual(result["coverage"]["unmatched_zips"], ["99999"])
        self.assertEqual(result["coverage"]["invalid_zips"], [""])

    def test_non_string_id_and_bad_request_are_rejected_before_network(self):
        for request in [{"zips":[2134]}, {"zips":"02134"}, {"layers":["https://example.com"]}, {"congress":"121"}, {"bbox":[-180,0,181,60]}, {"bbox":[0,0,float('nan'),60]}]:
            with self.subTest(request=request), patch.object(geo, "_download") as download:
                with self.assertRaises(geo.GeographyError): geo.geography(request)
                download.assert_not_called()

    def test_sql_tokens_cannot_enter_geometry_query(self):
        with patch.object(geo, "_gazetteer", self.gazetteer), patch.object(geo,"_features") as features:
            result = geo.geography({"zips": ["0' OR 1=1"], "layers": ["zip_areas"]})
        features.assert_not_called()
        self.assertEqual(result["coverage"]["invalid_zips"], ["0' OR 1=1"])

    def test_overlays_do_not_change_points_or_receive_amounts(self):
        provenance = {"url":"https://example.invalid/public-geometry","sha256":"b"*64}
        request = {"zips":["02134","00601"], "layers":[], "amount_cents":999999999}
        with patch.object(geo,"_gazetteer",self.gazetteer), patch.object(geo,"_features",return_value=(collection([]),provenance)):
            plain=geo.geography(request)
            layered=geo.geography({**request,"layers":["states","districts"],"congress":"120"})
        self.assertEqual(plain["points"],layered["points"])
        self.assertNotIn("amount",json.dumps(layered))
        self.assertEqual(layered["congress"],"120")

    def test_all_points_retained_when_polygon_budget_is_deferred(self):
        zips=[f'{n:05}' for n in range(501)]
        gaz={"data":{z:[-70,42] for z in zips},"sha256":"a"*64,"fetched_at":"now"}
        with patch.object(geo,"_gazetteer",return_value=gaz),patch.object(geo,"_features") as features:
            result=geo.geography({"zips":zips,"layers":["zip_areas"]})
        self.assertEqual(len(result["points"]),501)
        self.assertEqual(len(result["coverage"]["deferred_polygon_zips"]),501)
        self.assertFalse(result["coverage"]["polygon_complete"])
        features.assert_not_called()

    def test_viewport_polygon_limit_is_reported_not_silent(self):
        zips=[f'{n:05}' for n in range(502)]
        gaz={"data":{z:[-70,42] for z in zips},"sha256":"a"*64,"fetched_at":"now"}
        with patch.object(geo,"_gazetteer",return_value=gaz),patch.object(geo,"_features",return_value=(collection(zips[:501]),{})):
            result=geo.geography({"zips":zips,"layers":["zip_areas"],"bbox":[-71,41,-69,44]})
        self.assertEqual(len(result["points"]),502)
        self.assertEqual(len(result["layers"]["zip_areas"]["features"]),500)
        self.assertFalse(result["coverage"]["polygon_complete"])
        self.assertTrue(any("Zoom further" in warning for warning in result["warnings"]))

    def test_network_failure_preserves_unknown_coverage(self):
        with patch.object(geo,"_gazetteer",side_effect=OSError("offline")),patch.object(geo,"_features",side_effect=OSError("offline")):
            result=geo.geography({"zips":["02134"],"layers":["states"]})
        self.assertEqual(result["coverage"]["unmatched_zips"],["02134"])
        self.assertIn("points",result["errors"])
        self.assertIn("states",result["errors"])

    def test_cache_detects_corruption_and_rebuilds_atomically(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(geo,"CACHE",Path(tmp)):
            first=geo._cached("fixture",lambda:{"value":1})
            file=next(Path(tmp).glob("*.json"))
            changed=json.loads(file.read_text()); changed["data"]["value"]=2;file.write_text(json.dumps(changed))
            geo._MEMORY.clear()
            second=geo._cached("fixture",lambda:{"value":3})
            self.assertEqual(second["data"]["value"],3)
            self.assertNotEqual(first["sha256"],second["sha256"])
            self.assertFalse(list(Path(tmp).glob("*.tmp")))

    def test_wrong_vintage_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(geo,"CACHE",Path(tmp)),patch.object(geo,"_json_url",return_value={"name":"121st Congressional Districts","description":"January 1, 2028"}):
            with self.assertRaises(geo.GeographyError): geo._source_metadata("districts120")

    def test_non_census_download_is_rejected(self):
        with self.assertRaises(geo.GeographyError): geo._download("https://example.com/geometry")

    def test_census_undefined_district_water_area_is_retained(self):
        features=collection([f'{n:04}' for n in range(435)]+['33ZZ'])
        for feature in features['features']: feature['properties']['CDSESSN']='119'
        with tempfile.TemporaryDirectory() as tmp,patch.object(geo,'CACHE',Path(tmp)),patch.object(geo,'_source_metadata',return_value={'data':{'description':'119th Congress'}}),patch.object(geo,'_json_url',return_value=features):
            result,_=geo._features('districts119')
        self.assertEqual(result['features'][-1]['properties']['GEOID'],'33ZZ')
        self.assertTrue(result['features'][-1]['properties']['district_not_defined'])

    def test_native_map_money_conservation_and_insets(self):
        node=shutil.which("node")
        if not node:
            candidate=Path.home()/".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
            node=str(candidate) if candidate.exists() else None
        if not node: self.skipTest("Node runtime unavailable")
        code="""
const assert=require('assert/strict');const map=require(process.argv[1]);
const groups=[{zip5:'02134',state:'MA',amount_cents:10000},{zip5:'02134',state:'NY',amount_cents:5000},{zip5:'00601',state:'PR',amount_cents:-2500},{zip5:'99999',state:'MA',amount_cents:700},{zip5:'',state:'MA',amount_cents:800}];
const geo={points:[{zip5:'02134',longitude:-71,latitude:42},{zip5:'00601',longitude:-66.7,latitude:18.2}]};
const plain=map.prepare(groups,geo);const layered=map.prepare(groups,{...geo,layers:{states:{features:[{properties:{STUSAB:'MA',INTPTLON:-71,INTPTLAT:42}}]},districts:{features:[]},zip_areas:{features:[]}}});
assert.equal(plain.selected_cents,14000);assert.equal(plain.mapped_cents,12500);assert.equal(plain.unmapped_cents,1500);assert.equal(plain.marks.length,2);assert.deepEqual(plain,layered);
assert.equal(plain.marks.find(m=>m.zip5==='02134').amount_cents,15000);assert.throws(()=>map.prepare([{zip5:'02134',amount_cents:1.5}],geo));
for(const p of [[-149,61],[-157,21],[-66,18],[144,13],[-170,-14]]){const v=map.project(...p);assert.ok(v[0]>=0&&v[0]<=1000&&v[1]>=0&&v[1]<=640,JSON.stringify(v));}
const n=Array.from({length:10000},(_,i)=>({zip5:String(i).padStart(5,'0'),state:'MA',amount_cents:1}));const many=map.prepare(n,{points:n.map(g=>({zip5:g.zip5,longitude:-71,latitude:42}))});assert.equal(many.marks.length,10000);assert.equal(many.selected_cents,10000);
"""
        result=subprocess.run([node,"-e",code,str(APP/"contribution-map.js")],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == "__main__": unittest.main()
