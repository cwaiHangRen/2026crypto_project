#!/usr/bin/env python3
"""Prepare the round-3 development dataset without touching prior data.

The six existing pilot records are referenced in place. Remaining records are
deterministic, explicitly labelled synthetic fixtures; they are not variants of
the pilot files and share a source_family_id only when made from one template.
"""
from __future__ import annotations
import argparse, hashlib, json, os, struct, zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "data" / "external-pilot-20261006"

def sha256(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()

def png(path, w, h, seed, mode=0):
    from PIL import Image, ImageDraw, ImageFont
    import random
    rng=random.Random(seed)
    im=Image.new('RGB',(w,h),(248,248,244)); d=ImageDraw.Draw(im)
    font=ImageFont.load_default(size=12)
    if mode==0:
        for _ in range(5000):
            x,y=rng.randrange(w),rng.randrange(h); radius=rng.randrange(2,20)
            d.ellipse((x,y,x+radius,y+radius),fill=tuple(rng.randrange(256) for _ in range(3)))
    elif mode==1:
        for y in range(h):
            value=210+int(30*y/h); d.line((0,y,w,y),fill=(value,value,230+seed%20))
        for _ in range(4):
            x,y=rng.randrange(w),rng.randrange(h); d.ellipse((x,y,x+90,y+90),fill=(222,224,220+seed%25))
    elif mode==2:
        for k in range(12):
            x=35+k*(w-70)//12; height=rng.randrange(40,max(41,h-100))
            d.rectangle((x,h-height-30,x+(w-70)//16,h-30),fill=(30+k*15,70+seed%100,140))
            d.text((x,h-25),str(height),fill='black',font=font)
    elif mode==3:
        for k in range(60):
            x,y=rng.randrange(w),rng.randrange(h)
            d.line((x,0,w-x,h),fill=(20+seed,30,60),width=1)
            d.line((0,y,w,h-y),fill=(150,160,180),width=1)
    elif mode==4:
        for y in range(20,h-20,18):
            values=' '.join(str(rng.randrange(10000,99999)) for _ in range(max(1,w//45)))
            d.text((15,y),f'Record {seed} line {y}: {values}',fill='black',font=font)
    else:
        for y in range(20,h-30,35):
            d.line((20,y,w-20,y),fill='gray',width=1)
            for x in range(20,w-40,100):
                d.line((x,20,x,h-30),fill='gray',width=1)
                d.text((x+4,y+6),str(rng.randrange(100,9999)),fill='black',font=font)
    im.save(path)


def pdf(path, title, n_pages, seed):
    from reportlab.pdfgen.canvas import Canvas
    import random
    rng=random.Random(seed)
    c=Canvas(str(path),pagesize=(612,792),invariant=1)
    for page in range(n_pages):
        c.setFont('Helvetica',18); c.drawString(40,750,title)
        c.setFont('Helvetica',10)
        c.drawString(40,725,f'Synthetic development fixture {seed}; page {page+1}')
        for line in range(10):
            c.drawString(40,700-line*16,f'Observation {line+1}: measured value {rng.uniform(1,900):.3f}; synthetic diagnostic content.')
        for row in range(9):
            y=505-row*22; c.line(40,y,570,y)
            for col in range(5):
                x=40+col*106; c.line(x,329,x,505)
                c.drawString(x+6,y-15,str(rng.randrange(1000,9999)))
        c.drawString(40,295,'Synthetic bar chart')
        for k in range(10):
            c.setFillColorRGB(.1+k*.05,.3,.6); c.rect(45+k*49,100,30,rng.randrange(20,170),fill=1,stroke=0)
        c.setFillColorRGB(0,0,0); c.bookmarkPage(f'page-{page}'); c.addOutlineEntry(f'Page {page+1}',f'page-{page}')
        c.showPage()
    c.save()

def record(sample_id, family, media, origin, source, license_ref, status, local, dims=None, pages=None, category='unknown', claim='', split='development', support='in_scope', notes=''):
    p=Path(local); d={'sample_id':sample_id,'source_family_id':family,'media_type':media,'origin_category':origin,'source_reference':source,'license_reference':license_ref,'license_status':status,'local_path':str(p.relative_to(ROOT)).replace('\\','/'),'sha256':sha256(p)}
    d['dimensions']=dims; d['page_count']=pages; d['categories']=category; d['generator_claim']=claim; d['split']=split; d['support_status']=support; d['notes']=notes
    return d

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',default='data/round3-development-v3'); args=ap.parse_args()
    out=ROOT/args.output; out.mkdir(parents=True,exist_ok=True); (out/'images').mkdir(exist_ok=True); (out/'pdfs').mkdir(exist_ok=True)
    rec=[]
    existing=[('img-real-coco-139','coco-val2017-000000000139','image/jpeg','自然图片','https://images.cocodataset.org/val2017/000000000139.jpg','https://cocodataset.org/#termsofuse','review',PILOT/'coco/000000000139.jpg',(640,426),'photograph'),('img-real-coco-285','coco-val2017-000000000285','image/jpeg','自然图片','https://images.cocodataset.org/val2017/000000000285.jpg','https://cocodataset.org/#termsofuse','review',PILOT/'coco/000000000285.jpg',(586,640),'photograph'),('img-real-coco-632','coco-val2017-000000000632','image/jpeg','自然图片','https://images.cocodataset.org/val2017/000000000632.jpg','https://cocodataset.org/#termsofuse','review',PILOT/'coco/000000000632.jpg',(640,483),'photograph'),('img-doclaynet-example','doclaynet-official-example-132a855e','image/png','未知','https://github.com/DS4SD/DocLayNet/blob/main/assets/132a855ee8b23533d8ae69af0049c038171a06ddfcac892c3c6d7e6b4091c642.png','https://github.com/DS4SD/DocLayNet','review',PILOT/'doclaynet/official-example-page.png',(700,906),'document page'),('img-genimage-visualization','genimage-official-visualization-example','image/png','未知','https://raw.githubusercontent.com/GenImage-Dataset/GenImage/main/Examples/visulization.png','https://genimage-dataset.github.io/','review',PILOT/'genimage/official-visualization-example.png',(2807,1478),'AI example collage')]
    for sid,fam,mt,org,src,lr,st,p,dims,cat in existing: rec.append(record(sid,fam,mt,org,src,lr,st,p,dims=dims,category=cat,claim='source generator unknown; credential generator=gm-provenance (if signed)',notes='Existing pilot; review license. Visualization is not a real AI sample.' if 'visualization' in sid else 'Existing pilot record.'))
    pdf0=PILOT/'arxiv/doclaynet-paper-2206.01062v1.pdf'; rec.append(record('pdf-real-arxiv-doclaynet','arxiv-2206.01062v1','application/pdf','未知','arXiv 2206.01062v1','https://arxiv.org/abs/2206.01062v1','review',pdf0,pages=9,category='research paper',claim='source generator unknown; credential generator=gm-provenance (if signed)',notes='Existing pilot; redistribution license review pending.'))
    for i in range(55):
        w,h=((2048,1536) if i%11==0 else (640+(i%4)*320,480+(i%3)*240)); p=out/'images'/f'synth-image-{i+1:03d}.png'; mode=i%6; png(p,w,h,i+1,mode); fam=f'synthetic-image-type-{mode+1}'; rec.append(record(f'img-synth-{i+1:03d}',fam,'image/png','程序合成','local deterministic fixture generator', 'internal test fixture; no external license','internal-test-only',p,dims=(w,h),category=['procedural high texture','low texture','bar chart','fine lines','small text','table'][mode],claim='source generator=prepare_round3_dataset.py; credential generator=gm-provenance (separate)',notes='Synthetic fixture; family denotes a distinct procedural layout/content type.'))
    for i in range(39):
        p=out/'pdfs'/f'synth-document-{i+1:03d}.pdf'; pdf(p,f'Round3 document {i+1}',1+(i%3),i+1); fam='synthetic-pdf-report-table-chart'; rec.append(record(f'pdf-synth-{i+1:03d}',fam,'application/pdf','程序合成','local deterministic PDF fixture generator','internal test fixture; no external license','internal-test-only',p,pages=1+(i%3),category='text/table document' if i%2 else 'mixed text and chart',claim='source generator=prepare_round3_dataset.py; credential generator=gm-provenance (separate)',notes='Synthetic PDF; all PDFs share one report/table/chart template family.'))
    assert len(rec)==100
    assert len(rec)==100 and len({r['sample_id'] for r in rec})==100
    for r in rec: r['file_size_bytes']= (ROOT/r['local_path']).stat().st_size
    (out/'dataset_manifest.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rec)+'\n',encoding='utf-8')
    summary={'schema_version':'round3-development-3','target_count':100,'actual_count':len(rec),'unique_sha256':len({x['sha256'] for x in rec}),'duplicate_rows':len(rec)-len({x['sha256'] for x in rec}),'unique_source_family_id':len({x['source_family_id'] for x in rec}),'images':sum(x['media_type'].startswith('image') for x in rec),'pdfs':sum(x['media_type']=='application/pdf' for x in rec),'existing_pilot':6,'synthetic':94,'license_review':sum(x['license_status']=='review' for x in rec),'synthetic_families':sorted({x['source_family_id'] for x in rec if x['origin_category']=='程序合成'}),'limitations':'Only six existing pilot files; external licenses remain review and synthetic records are internal fixtures.'}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
