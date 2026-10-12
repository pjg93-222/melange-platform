"""Public Census geometry for contribution maps; never receives or totals money.

Geography is cached independently of contribution facts. ZIP locations are Census
ZCTA internal points, not donor addresses. Congressional outlines do not assign
donors to districts. No collectors or production databases run on import.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import threading
import urllib.parse
import urllib.request
import uuid
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
CACHE = PROJECT / "state" / "geography" / "census-v1"
GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_zcta_national.zip"
BASE = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
SOURCES = {
    "states": {"url": BASE + "State_County/MapServer/36", "vintage": "ACS 2025 - January 1, 2025", "name": "States", "description": "January 1, 2025", "fields": "GEOID,NAME,STUSAB,INTPTLAT,INTPTLON", "offset": .012},
    "districts119": {"url": BASE + "Legislative/MapServer/8", "vintage": "119th Congress - ACS 2025, January 1, 2025", "name": "119th Congressional Districts", "description": "January 1, 2025", "fields": "GEOID,NAME,STATE,CDSESSN,INTPTLAT,INTPTLON", "offset": .005},
    "districts120": {"url": BASE + "Legislative/MapServer/0", "vintage": "120th Congress - January 1, 2026", "name": "120th Congressional Districts", "description": "January 1, 2026", "fields": "GEOID,NAME,STATE,CDSESSN,INTPTLAT,INTPTLON", "offset": .005},
    "zip_areas": {"url": BASE + "PUMA_TAD_TAZ_UGA_ZCTA/MapServer/7", "vintage": "2020 Census ZCTAs - January 1, 2020", "name": "2020 Census ZIP Code Tabulation Areas", "description": "January 1, 2020", "fields": "GEOID,NAME,INTPTLAT,INTPTLON", "offset": .0006},
}
MAX_ZIPS = 50000
POLYGON_LIMIT = 500
_LOCK = threading.RLock()
_MEMORY: dict = {}


class GeographyError(ValueError):
    """A geography request or public source cannot safely be used."""


def _digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _download(url, *, limit=24000000):
    # URLs are constructed only from fixed official sources and validated IDs.
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www2.census.gov", "tigerweb.geo.census.gov"}:
        raise GeographyError("Only official Census geography sources are permitted.")
    request = urllib.request.Request(url, headers={"User-Agent": "Mentat-local-geography/1.0", "Accept": "application/json,*/*"})
    with urllib.request.urlopen(request, timeout=25) as response:
        final = urllib.parse.urlsplit(response.url)
        if final.scheme != "https" or final.hostname not in {"www2.census.gov", "tigerweb.geo.census.gov"}:
            raise GeographyError("The Census download redirected outside its official hosts.")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise GeographyError("The geometry response exceeded its size limit; choose a smaller map area.")
    return data


def _json_url(url):
    data = json.loads(_download(url))
    if not isinstance(data, dict) or data.get("error"):
        raise GeographyError("The Census geometry service returned an error.")
    return data


def _cached(key, build):
    """Content-checked, atomic cache. No expiry can silently change saved vintage."""
    key = hashlib.sha256(key.encode()).hexdigest()
    location = CACHE / (key + ".json")
    token = (str(CACHE), key)
    with _LOCK:
        if token in _MEMORY:
            return _MEMORY[token]
        try:
            record = json.loads(location.read_text(encoding="utf-8"))
            if record.get("sha256") == _digest(record["data"]):
                _MEMORY[token] = record
                return record
        except (OSError, ValueError, KeyError, TypeError):
            pass
        data = build()
        record = {"data": data, "sha256": _digest(data), "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        CACHE.mkdir(parents=True, exist_ok=True)
        temporary = location.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(json.dumps(record, separators=(",", ":"), allow_nan=False), encoding="utf-8")
            os.replace(temporary, location)
        finally:
            temporary.unlink(missing_ok=True)
        _MEMORY[token] = record
        return record


def _gazetteer():
    def build():
        archive = zipfile.ZipFile(io.BytesIO(_download(GAZETTEER, limit=4000000)))
        files = [entry for entry in archive.infolist() if entry.filename.lower().endswith(".txt")]
        if len(files) != 1 or files[0].file_size > 12000000:
            raise GeographyError("Unexpected Census ZIP gazetteer archive.")
        content = archive.read(files[0]).decode("utf-8-sig")
        result = {}
        for row in csv.DictReader(io.StringIO(content), delimiter="|"):
            zip5 = row["GEOID"].strip()
            longitude, latitude = float(row["INTPTLONG"]), float(row["INTPTLAT"])
            if re.fullmatch(r"\d{5}", zip5, re.ASCII) and -180 <= longitude <= 180 and -90 <= latitude <= 90:
                result[zip5] = [longitude, latitude]
        if len(result) < 30000:
            raise GeographyError("Census ZIP gazetteer is unexpectedly incomplete.")
        return result
    return _cached("gazetteer:" + GAZETTEER, build)


def _source_metadata(key, *, refresh=False):
    source = SOURCES[key]
    def build():
        metadata = _json_url(source["url"] + "?f=json")
        if metadata.get("name") != source["name"] or source["description"] not in metadata.get("description", ""):
            raise GeographyError("The Census layer changed its name or vintage; it requires an explicit catalog update.")
        return {"name": metadata["name"], "description": metadata["description"]}
    if refresh:
        return {"data": build()}
    return _cached("metadata:" + source["url"] + ":" + source["vintage"], build)


def _features(key, *, zips=None, bbox=None):
    source = SOURCES[key]
    metadata = _source_metadata(key)
    parameters = {"f": "geojson", "where": "1=1", "outFields": source["fields"], "outSR": "4326",
                  "returnGeometry": "true", "maxAllowableOffset": source["offset"], "geometryPrecision": 5,
                  "orderByFields": "GEOID", "resultRecordCount": POLYGON_LIMIT + 1 if key == "zip_areas" else 1000}
    if key.startswith("districts"):
        parameters["where"] = "CDSESSN='" + key[-3:] + "'"
    if zips is not None:
        # zips has already passed strict five-digit validation; no user SQL.
        parameters["where"] = "GEOID IN (" + ",".join("'" + z + "'" for z in zips) + ")"
    if bbox is not None:
        parameters.update(geometry=",".join(str(x) for x in bbox), geometryType="esriGeometryEnvelope", inSR="4326", spatialRel="esriSpatialRelIntersects")
    url = source["url"] + "/query?" + urllib.parse.urlencode(parameters)
    def build():
        # TIGERweb layer numbers can move between releases. A newly requested
        # viewport must revalidate the actual source even when earlier geometry
        # from this named vintage is already cached. Existing cached geometry
        # remains usable offline and its content hash is unchanged.
        _source_metadata(key, refresh=True)
        collection = _json_url(url)
        if collection.get("type") != "FeatureCollection" or not isinstance(collection.get("features"), list):
            raise GeographyError("Census returned an invalid GeoJSON collection.")
        features = collection["features"]
        if key != "zip_areas" and (collection.get("exceededTransferLimit") or len(features) < (50 if key == "states" else 435)):
            raise GeographyError("The Census boundary collection is incomplete.")
        for feature in features:
            properties = feature.get("properties") or {}
            geometry = feature.get("geometry") or {}
            expected_length = 2 if key == "states" else 5 if key == "zip_areas" else 4
            identifier_pattern = r"[0-9]{2}(?:[0-9]{2}|ZZ)" if key.startswith("districts") else r"[0-9]{" + str(expected_length) + r"}"
            if not re.fullmatch(identifier_pattern, str(properties.get("GEOID", "")), re.ASCII):
                raise GeographyError("Census returned an invalid geographic identifier.")
            # Census includes water/other areas with no defined district, coded
            # state-FIPS + ZZ. Preserve the official label; never invent a seat.
            if key.startswith("districts") and str(properties["GEOID"]).endswith("ZZ"):
                properties["district_not_defined"] = True
            if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
                raise GeographyError("Census returned an unsupported boundary geometry.")
            if key.startswith("districts") and str(properties.get("CDSESSN")) != key[-3:]:
                raise GeographyError("Congressional boundary does not match the requested Congress.")
        return collection
    record = _cached("features:" + url, build)
    provenance = {"url": source["url"], "query_url": url, "vintage": source["vintage"], "description": metadata["data"]["description"],
                  "sha256": record["sha256"], "fetched_at": record["fetched_at"], "simplification_degrees": source["offset"]}
    return record["data"], provenance


def geo_catalog():
    return {"schema_version": 1, "geography_kind": "donor_recorded_zip", "congresses": ["119", "120"], "default_congress": "119",
            "layers": {key: {k: value[k] for k in ("url", "vintage", "name")} for key, value in SOURCES.items()},
            "zip_points": {"url": GAZETTEER, "vintage": "2025 Gazetteer, Census ZCTA internal points", "meaning": "Representative ZIP-area points; not donor addresses."},
            "polygon_limit": POLYGON_LIMIT, "max_zips": MAX_ZIPS,
            "notes": ["State and district outlines never allocate contribution amounts.", "ZCTAs approximate ZIP geography; not every postal ZIP has a ZCTA.",
                      "Congress and geometry hashes are returned with each view.", "Geometry is simplified for display, not precise address-to-district assignment."]}


def _request(request):
    if not isinstance(request, dict):
        raise GeographyError("Geography request must be an object.")
    raw = request.get("zips", [])
    if not isinstance(raw, list) or len(raw) > MAX_ZIPS:
        raise GeographyError("zips must be a list of at most 50,000 ZIP strings.")
    valid, invalid = set(), set()
    for value in raw:
        if not isinstance(value, str):
            raise GeographyError("ZIP identifiers must be strings so leading zeros remain intact.")
        value = value.strip()
        if len(value) > 20:
            raise GeographyError("Invalid ZIP identifier.")
        (valid if re.fullmatch(r"[0-9]{5}", value) else invalid).add(value)
    layers = request.get("layers", ["states"])
    if not isinstance(layers, list) or any(layer not in {"states", "districts", "zip_areas"} for layer in layers):
        raise GeographyError("Unknown geography layer.")
    congress = str(request.get("congress", "119"))
    if congress not in {"119", "120"}:
        raise GeographyError("Congress must be 119 or 120.")
    bbox = request.get("bbox")
    if bbox is not None:
        if not isinstance(bbox, list) or len(bbox) != 4 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in bbox):
            raise GeographyError("bbox must contain four finite longitude/latitude values.")
        if not (-180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90):
            raise GeographyError("bbox must be west, south, east, north within geographic bounds.")
    return sorted(valid), sorted(invalid), list(dict.fromkeys(layers)), congress, bbox


def geography(request):
    zips, invalid, layer_names, congress, bbox = _request(request)
    response = {"schema_version": 1, "geography_kind": "donor_recorded_zip", "congress": congress,
                "points": [], "layers": {}, "sources": [], "warnings": [], "errors": {},
                "coverage": {"requested_zips": len(zips), "invalid_zips": invalid, "located_zips": 0, "unmatched_zips": [],
                             "polygon_zips": [], "deferred_polygon_zips": [], "polygon_complete": "zip_areas" not in layer_names}}
    try:
        gazetteer = _gazetteer()
        coordinates = gazetteer["data"]
        response["points"] = [{"zip5": z, "zip": z, "longitude": coordinates[z][0], "latitude": coordinates[z][1]} for z in zips if z in coordinates]
        response["coverage"]["located_zips"] = len(response["points"])
        response["coverage"]["unmatched_zips"] = [z for z in zips if z not in coordinates]
        response["sources"].append({"url": GAZETTEER, "vintage": "2025 Gazetteer, ZCTA representative points", "sha256": gazetteer["sha256"], "fetched_at": gazetteer["fetched_at"]})
    except Exception as error:
        response["errors"]["points"] = str(error)
        response["coverage"]["unmatched_zips"] = zips
        response["warnings"].append("ZIP locations are unavailable. Contribution totals remain in the table; retry when Census is reachable.")
    for layer in layer_names:
        key = "districts" + congress if layer == "districts" else layer
        if layer == "zip_areas" and not zips:
            response["layers"][layer] = {"type": "FeatureCollection", "features": []}
            response["coverage"]["polygon_complete"] = True
            continue
        if layer == "zip_areas" and len(zips) > POLYGON_LIMIT and bbox is None:
            response["coverage"]["deferred_polygon_zips"] = zips
            response["warnings"].append("ZIP symbols show every located ZIP. Zoom into a region or select at most 500 ZIPs to load ZIP-area polygons.")
            continue
        try:
            collection, provenance = _features(key, zips=zips if layer == "zip_areas" and len(zips) <= POLYGON_LIMIT else None, bbox=bbox if layer == "zip_areas" else None)
            if layer == "zip_areas":
                selected = set(zips)
                partial = bool(collection.get("exceededTransferLimit")) or len(collection["features"]) > POLYGON_LIMIT
                features = [f for f in collection["features"][:POLYGON_LIMIT] if f["properties"]["GEOID"] in selected]
                collection = {"type": "FeatureCollection", "features": features}
                polygon_zips = sorted({f["properties"]["GEOID"] for f in features})
                response["coverage"].update(polygon_zips=polygon_zips, deferred_polygon_zips=sorted(selected - set(polygon_zips)), polygon_complete=not partial and len(polygon_zips) == len(selected))
                if partial:
                    response["warnings"].append("This viewport contains more than 500 ZIP areas. Zoom further for complete polygon coverage; all located ZIP symbols and all amounts are retained.")
            response["layers"][layer] = collection
            response["sources"].append(provenance)
        except Exception as error:
            response["errors"][layer] = str(error)
            response["warnings"].append(layer.replace("_", " ").title() + " geometry is unavailable. Cached layers and contribution amounts are unchanged.")
    if response["coverage"]["unmatched_zips"] or invalid:
        response["warnings"].append("Some recorded ZIPs have no Census ZCTA location. Their amounts remain in unmapped coverage; they are not zero giving.")
    response["warnings"].append("ZIP points and areas represent Census postal-area geography, not donor addresses. District and state overlays do not allocate money.")
    return response
