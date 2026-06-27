# -*- coding: utf-8 -*-
import os, sys, json
import numpy as np
import fitz
from multiprocessing import Pool

PDF = "/tmp/zhuankan.pdf"
OUT = "/tmp/ocr_cache"
DPI = 220
os.makedirs(OUT, exist_ok=True)

_engine = None
_doc = None

def init():
    global _engine, _doc
    from rapidocr_onnxruntime import RapidOCR
    _engine = RapidOCR()
    _doc = fitz.open(PDF)

def work(i):
    out = f"{OUT}/page_{i:04d}.json"
    if os.path.exists(out):
        return (i, -1)
    pix = _doc.load_page(i).get_pixmap(dpi=DPI)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    img = np.ascontiguousarray(img[:, :, ::-1])  # RGB->BGR
    res, _ = _engine(img)
    rows = []
    if res:
        for box, txt, conf in res:
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            rows.append([round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys)),
                         txt, round(float(conf), 3)])
    json.dump({"page": i, "w": pix.width, "h": pix.height, "items": rows},
              open(out, "w"), ensure_ascii=False)
    return (i, len(rows))

if __name__ == "__main__":
    n = fitz.open(PDF).page_count
    todo = [i for i in range(n) if not os.path.exists(f"{OUT}/page_{i:04d}.json")]
    print("total pages:", n, "todo:", len(todo), flush=True)
    done = 0
    with Pool(4, initializer=init) as p:
        for (i, c) in p.imap_unordered(work, todo):
            done += 1
            if done % 10 == 0 or done == len(todo):
                print(f"progress {done}/{len(todo)} last_page={i+1} blocks={c}", flush=True)
    print("ALL DONE", flush=True)
