from gm_provenance.attack_lab import AttackCase, attack_summary, run_attack_matrix, write_attack_matrix
from gm_provenance.media_adapters import ImageAdapter
from PIL import Image
import io

CID = "0123456789abcdef0123456789abcdef"

def _image():
    im=Image.new("RGB",(512,512),(120,120,120)); out=io.BytesIO(); im.save(out,"PNG"); return out.getvalue()

def test_attack_matrix_json_csv(tmp_path):
    adapter=ImageAdapter(); marked=adapter.embed(_image(),CID).final_bytes
    cases=[AttackCase("metadata","image","metadata_clear",{}), AttackCase("replace","image","local_replace",{})]
    rows=run_attack_matrix(marked,CID,cases,adapter=adapter,output_dir=tmp_path)
    assert len(rows)==2
    assert attack_summary(rows)["total"] == 2
    j=write_attack_matrix(rows,tmp_path/"attacks.json"); c=write_attack_matrix(rows,tmp_path/"attacks.csv")
    assert j["count"] == c["count"] == 2
    assert (tmp_path/"attacks.csv").read_text(encoding="utf-8").startswith("attack_id")
