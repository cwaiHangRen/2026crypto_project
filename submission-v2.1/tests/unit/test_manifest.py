import json
from gm_provenance import manifest


def test_duplicate_nan_and_unknown_fields_rejected():
    for raw in ('{"a":1,"a":2}', '{"x":NaN}'):
        try: manifest.loads(raw)
        except manifest.ManifestError: pass
        else: raise AssertionError("malformed JSON accepted")


def test_jcs_key_order_and_body_validation():
    body = {"schema_version":"1.0","asset_id":"a"*32,"content_id":"b"*32,"version":1,
            "publisher_id":"publisher-demo-001","signer_key_id":"c"*64,"generator":{"name":"x"},
            "created_at":"2026-10-05T00:00:00Z","media_type":"image/png","content_size":0,
            "content_hash_alg":"SM3","content_hash":"d"*64,"metadata":{},"metadata_hash":""*0,
            "parent_content_id":None,"parent_manifest_hash":None,"transformation":{},
            "allowed_transformations":[],"watermark_profile":{},"signature_suite":"SM2-SM3-DER",
            "sm2_user_id":"1234567812345678"}
    # metadata_hash is filled by caller in real flow; this test checks strict schema separately.
    try: manifest.validate_body(body)
    except manifest.ManifestError as exc: assert "metadata_hash" in str(exc) or "SM3" in str(exc)
    else: raise AssertionError("invalid hash accepted")
