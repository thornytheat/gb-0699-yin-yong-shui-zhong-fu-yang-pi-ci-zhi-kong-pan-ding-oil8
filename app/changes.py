"""质控重判影响：把复测后的全部质控项与上一版结论逐项对照。

kind 对应四类质控证据；before/after 为原因码，changed 标记是否变化，
recalculated 标记该质控项是否落在本次版本变化的判定范围内。
"""
from app.scope import check_in_scope

COLLECTIONS=(
    ("parallel","平行样","parallel"),
    ("recovery","加标回收","recoveries"),
    ("blank","空白","blank_checks"),
    ("release","批次放行","released"),
)
LABELS=dict((kind,label) for kind,label,_ in COLLECTIONS)

def _key(kind,row):
    if kind=="blank":
        return (kind,row.get("batch",""),row.get("analyte",""),row.get("id",""))
    if kind=="release":
        return (kind,row.get("batch",""),row.get("analyte",""))
    return (kind,row.get("batch",""),row.get("analyte",""),row.get("pair",""))

def _index(result):
    out={}
    for kind,_label,field in COLLECTIONS:
        for row in result.get(field,[]) or []:
            out[_key(kind,row)]=row
    return out

def compare(previous,current):
    before=_index(previous or {});after=_index(current or {});rows=[]
    for key in sorted(set(before)|set(after),key=str):
        b=before.get(key);a=after.get(key)
        rows.append({"kind":key[0],"batch":key[1],"analyte":key[2],
                     "scope":str(key[3]) if len(key)>3 else "",
                     "before":b.get("status") if b else None,
                     "after":a.get("status") if a else None,
                     "changed":(b.get("status") if b else None)!=(a.get("status") if a else None)})
    return rows

def impact(previous,current,keys):
    rows=[]
    for row in compare(previous,current):
        if not check_in_scope(row["kind"],{"batch":row["batch"],"analyte":row["analyte"],
                                           "pair":row["scope"],"id":row["scope"]},keys):
            continue
        rows.append(dict(row,recalculated=True,label=LABELS[row["kind"]]))
    return rows
