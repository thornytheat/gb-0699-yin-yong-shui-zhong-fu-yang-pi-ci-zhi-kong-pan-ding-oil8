from app.reader import read_rows
from app.report import report,quarantine
FIELDS="id batch analyte kind pair value spike unit version".split()

def version_key(value):
    """同一版本的写法差异（前导零、空白）按数值视为同版。"""
    text=str(value).strip()
    try:
        return ("n",int(text))
    except (TypeError,ValueError):
        return ("s",text)

def apply(ctx):
    rows,bad,blank=read_rows(ctx["text"],FIELDS);ctx["quarantine"].extend(bad);ctx["blank_rows"]=blank;ctx["input_count"]=len(rows)+len(bad)+blank
    out=[];seen=set()
    for r in rows:
        if not r["id"]:quarantine(ctx,r,"ID_REQUIRED");continue
        key=(r["batch"],r["analyte"],r["id"],version_key(r["version"]))
        if key in seen:quarantine(ctx,r,"VERSION_CONFLICT");continue
        seen.add(key);out.append(r)
    ctx["raw"]=out;ctx["intake_quarantine"]=[dict(x) for x in ctx["quarantine"]]
    return report(out,样品=len(out),隔离=len(ctx["quarantine"]))
