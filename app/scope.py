"""复测重算范围：由样品身份键扩散到受影响的质控项。

- 任一有效值变化都会影响所在 batch+analyte 的放行结论；
- 平行样/加标样影响所在 batch+analyte+pair 的平行与加标质控；
- 空白样只影响其自身的空白判定与所在批次放行。
"""
from app.versions import identity

QC_KINDS={"sample","duplicate","spike"}

def qc_keys(row):
    batch,analyte,_=identity(row)
    keys={("release",batch,analyte)}
    pair=str(row.get("pair","")).strip()
    kind=str(row.get("kind","")).strip()
    if kind in QC_KINDS and pair:
        keys.add(("pair",batch,analyte,pair))
    if kind=="blank":
        keys.add(("blank",batch,analyte,str(row.get("id","")).strip()))
    return keys

def affected_checks(rows,changed_ids):
    """返回受影响的质控键集合；changed_ids 为身份键字符串集合。

    rows 应包含同一身份的历史版本，旧配对号也会随之进入范围，
    这样样品移出旧配对组导致的配对缺失同样被重判。
    """
    keys=set()
    for row in rows:
        if "|".join(identity(row)) in changed_ids:
            keys|=qc_keys(row)
    return keys

def check_in_scope(kind,check,keys):
    batch=check.get("batch","");analyte=check.get("analyte","")
    if kind=="release":
        return ("release",batch,analyte) in keys
    if kind=="blank":
        return ("blank",batch,analyte,str(check.get("id",""))) in keys
    return ("pair",batch,analyte,check.get("pair")) in keys

def scope_summary(keys):
    return sorted(f"{k[0]}:{k[1]}/{k[2]}"+("/"+str(k[3]) if len(k)>3 else "") for k in keys)
