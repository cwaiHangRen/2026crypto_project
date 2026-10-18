from scripts.prepare_round3_dataset import png, pdf
from pypdf import PdfReader

def test_generated_pngs_are_pixel_distinct(tmp_path):
    a,b=tmp_path/'a.png',tmp_path/'b.png'; png(a,320,240,1,4); png(b,320,240,2,4)
    assert a.read_bytes()!=b.read_bytes()

def test_generated_pdf_has_extractable_text(tmp_path):
    p=tmp_path/'x.pdf'; pdf(p,'Fixture title',1,7); raw=p.read_bytes()
    text='\n'.join(page.extract_text() or '' for page in PdfReader(str(p)).pages)
    assert 'Synthetic development fixture 7' in text
    assert b'\\nstream' not in raw
