#!/usr/bin/env python3
"""Build offline Round 7 external challenge manifest from downloaded media."""
from pathlib import Path
import hashlib, json
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "round7-challenge-20261011"
MEDIA = DATA / "media"

SOURCES = [
("wikimedia-earthrise-cutout","earthrise","wikimedia-earthrise-cutout.jpg","https://commons.wikimedia.org/wiki/Special:FilePath/Earthrise_cutout.jpg","https://commons.wikimedia.org/wiki/File:Earthrise_cutout.jpg","Public domain (NASA)","earthrise"),
("wikimedia-apollo10-earthrise","apollo10","wikimedia-apollo10-earthrise.jpg","https://commons.wikimedia.org/wiki/Special:FilePath/Apollo_10_earthrise.jpg","https://commons.wikimedia.org/wiki/File:Apollo_10_earthrise.jpg","Public domain (NASA)","earthrise"),
("wikimedia-apollo10-earthrise-png","apollo10","wikimedia-apollo10-earthrise-png.png","https://commons.wikimedia.org/wiki/Special:FilePath/Apollo_10_earthrise.png","https://commons.wikimedia.org/wiki/File:Apollo_10_earthrise.png","Public domain (NASA)","earthrise"),
("wikimedia-artemis-earthrise","artemis","wikimedia-artemis-earthrise.jpg","https://commons.wikimedia.org/wiki/Special:FilePath/Artemis_Era_Earthrise_%28art002e009280b%29.jpg","https://commons.wikimedia.org/wiki/File:Artemis_Era_Earthrise_(art002e009280b).jpg","Public domain (NASA/JSC)","earthrise"),
("wikimedia-apollo17-blue-marble","apollo17","wikimedia-apollo17-blue-marble.jpg","https://upload.wikimedia.org/wikipedia/commons/3/39/The_Earth_seen_from_Apollo_17_%28Original_Orientation%29.jpg","https://commons.wikimedia.org/wiki/File:The_Earth_seen_from_Apollo_17_(Original_Orientation).jpg","Public domain (NASA)","earth"),
("wikimedia-earth-from-space","earth-space","wikimedia-earth-from-space.jpg","https://upload.wikimedia.org/wikipedia/commons/0/00/Earth_from_space.jpg","https://commons.wikimedia.org/wiki/File:Earth_from_space.jpg","Public domain (NASA)","earth"),
("wikimedia-landscape-of","landscape-of","wikimedia-landscape-of.jpg","https://commons.wikimedia.org/wiki/Special:FilePath/Landscape_of.jpg","https://commons.wikimedia.org/wiki/File:Landscape_of.jpg","CC BY-SA 4.0","landscape"),
("wikimedia-fronalpstock","fronalpstock","wikimedia-fronalpstock-thumb.jpg","https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/Fronalpstock_big.jpg/1280px-Fronalpstock_big.jpg","https://commons.wikimedia.org/wiki/File:Fronalpstock_big.jpg","CC BY-SA 3.0","landscape"),
("wikimedia-gull-portrait","gull","wikimedia-gull-portrait.jpg","https://upload.wikimedia.org/wikipedia/commons/9/9a/Gull_portrait_ca_usa.jpg","https://commons.wikimedia.org/wiki/File:Gull_portrait_ca_usa.jpg","CC BY 2.5","animal"),
("wikimedia-mona-lisa","mona-lisa","wikimedia-mona-lisa.jpg","https://upload.wikimedia.org/wikipedia/commons/6/6a/Mona_Lisa.jpg","https://commons.wikimedia.org/wiki/File:Mona_Lisa.jpg","Public domain","portrait"),
("wikimedia-mona-lisa-cc0","mona-lisa-cc0","wikimedia-mona-lisa-cc0.jpg","https://upload.wikimedia.org/wikipedia/commons/4/47/Mona_Lisa_Public_Domain.jpg","https://commons.wikimedia.org/wiki/File:Mona_Lisa_Public_Domain.jpg","CC0 1.0","portrait"),
("wikimedia-testimage1","testimage1","wikimedia-testimage1.png","https://thumb.wikimedia.org/wikipedia/commons/thumb/2/21/Testimage1.png/1280px-Testimage1.png","https://commons.wikimedia.org/wiki/File:Testimage1.png","CC BY-SA 4.0","fruit-photo"),
("wikimedia-binary-test","binary-test","wikimedia-binary-test.png","https://upload.wikimedia.org/wikipedia/commons/7/77/Binary_Test_Image.png","https://commons.wikimedia.org/wiki/File:Binary_Test_Image.png","CC0 1.0","test-pattern"),
("wikimedia-hummingbird-flying","chasingfeathers-hummingbird","wikimedia-hummingbird-flying.jpg","https://upload.wikimedia.org/wikipedia/commons/5/57/Hummingbird_Flying.jpg","https://commons.wikimedia.org/wiki/File:Hummingbird_Flying.jpg","CC BY-SA 4.0","animal"),
("wikimedia-hummingbird-feeder","msfrazzle-hummingbird","wikimedia-hummingbird-feeder.jpg","https://upload.wikimedia.org/wikipedia/commons/b/b8/Hummingbird_at_feeder%2C_sky_background.jpg","https://commons.wikimedia.org/wiki/File:Hummingbird_at_feeder,_sky_background.jpg","CC BY-SA 4.0","animal"),
]

def one(row):
 sid,fam,name,source,ref,lic,cat=row; p=MEDIA/name; b=p.read_bytes(); im=Image.open(p); im.load()
 excluded = {"wikimedia-apollo10-earthrise-png": "duplicate_apollo10_representation", "wikimedia-mona-lisa-cc0": "same_artwork_graphic_derivative", "wikimedia-binary-test": "authored_test_pattern_not_external_natural_challenge"}
 included = sid not in excluded
 try:
  sm3 = hashlib.new('sm3', b).hexdigest()
 except ValueError as exc:
  raise RuntimeError("当前 Python/OpenSSL 不提供 SM3，拒绝生成不完整清单") from exc
 generation = "external_author_test_pattern" if cat == "test-pattern" else "photographic_or_historical_public_image"
 d={"sample_id":sid,"source_family_id":fam,"source":source,"license":lic,"license_reference":ref,"acquisition":{"local_path":str(p.relative_to(ROOT)).replace('\\','/'),"bytes":len(b),"sha256":hashlib.sha256(b).hexdigest(),"sm3":sm3},"attrs":{"format":im.format,"width":im.width,"height":im.height,"mode":im.mode,"decode":"PIL.load_ok"},"category":cat,"generation_declared":generation,"used_for_tuning":False,"redistribution":"local_experiment_copy_source_page_license_recorded","challenge_included":included}
 if not included:
  d["exclusion_reason"] = excluded[sid]
  d["duplicate_of"] = {"wikimedia-apollo10-earthrise-png": "wikimedia-apollo10-earthrise", "wikimedia-mona-lisa-cc0": "wikimedia-mona-lisa"}.get(sid)
 return d

items=[one(x) for x in SOURCES]
manifest={"schema":"round7-external-challenge-manifest/v1","generated_offline":True,"challenge_id":"round7-challenge-20261011","license_evidence_policy":"source page references recorded from Commons; no license inferred beyond listed page evidence","items":items}
(DATA/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
summary={"challenge_id":manifest["challenge_id"],"total_files":len(items),"challenge_included":sum(x["challenge_included"] for x in items),"duplicates":sum(not x["challenge_included"] for x in items),"decode_ok":all(x["attrs"]["decode"]=="PIL.load_ok" for x in items),"categories":{c:sum(x["category"]==c for x in items) for c in sorted({x["category"] for x in items})},"excluded":[{"sample_id":x["sample_id"],"duplicate_of":x["duplicate_of"]} for x in items if not x["challenge_included"]],"ai_or_small_text_samples":[]}
(DATA/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(f"wrote {len(items)} items; included={summary['challenge_included']}")
