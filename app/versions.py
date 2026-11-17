"""复测版本裁决：按 batch+analyte+id 身份键选择最高有效版本。

规则：
- 新版本高于当前最高版本才生效（APPLIED）；
- 完全相同的版本与内容视为重试重放（REPLAY），不新增版本；
- 同版本不同内容隔离（VERSION_CONFLICT）；
- 晚到的低版本记录隔离（STALE_VERSION），不得让有效值倒退；
- 版本号无法解析隔离（VERSION）。
"""
from app.numbers import number

CONTENT_FIELDS=("kind","pair","value","spike","unit")

def identity(row):
    return (str(row.get("batch","")),str(row.get("analyte","")),str(row.get("id","")))

def id_key(row):
    return "|".join(identity(row))

def version_of(row):
    text=str(row.get("version","")).strip()
    try:
        value=number(text)
    except Exception:
        return None
    if value!=value.to_integral_value():
        return None
    return int(value)

def content(row):
    def norm(field):
        return str(row.get(field,"")).strip()
    return tuple(norm(f) for f in CONTENT_FIELDS)

def max_versions(rows):
    result={}
    for row in rows:
        version=version_of(row)
        if version is None:continue
        key=id_key(row);current=result.get(key)
        if current is None or version>current:result[key]=version
    return result

def merge(prior,incoming,prior_source="original",incoming_source="retest"):
    """把增量复测行并入已受理行。

    返回 merged（含历史版本的全部行）、events（逐行裁决）、accepted（本次生效行）、
    quarantined（[(row,reason)]）。来源标记写入 source，证据可按文件区分行号。
    """
    merged=[]
    for r in prior:
        item=dict(r);item.setdefault("source",prior_source);merged.append(item)
    index={};maxv={}
    for row in merged:
        key=identity(row);version=version_of(row)
        index.setdefault(key,{})[version]=row
        if version is not None:
            maxv[key]=max(maxv.get(key,version),version)
    events=[];accepted=[];quarantined=[]
    for row in incoming:
        row=dict(row);row.setdefault("source",incoming_source)
        key=identity(row);version=version_of(row)
        event={"id":row.get("id",""),"batch":key[0],"analyte":key[1],
               "version":row.get("version",""),"line":row.get("line",0),"source":row["source"]}
        if version is None:
            event["action"]="VERSION";quarantined.append((row,"VERSION"));events.append(event);continue
        if key not in maxv:
            # 新身份：该样品/检测项在本批次尚无任何有效版本，直接受理。
            merged.append(row);index.setdefault(key,{})[version]=row;maxv[key]=version
            event["action"]="APPLIED";accepted.append(row);events.append(event);continue
        existing=index.get(key,{}).get(version)
        if existing is not None:
            if content(existing)==content(row):
                event["action"]="REPLAY"
            else:
                event["action"]="VERSION_CONFLICT";quarantined.append((row,"VERSION_CONFLICT"))
            events.append(event);continue
        if version<maxv[key]:
            event["action"]="STALE_VERSION";quarantined.append((row,"STALE_VERSION"));events.append(event);continue
        merged.append(row);index.setdefault(key,{})[version]=row;maxv[key]=version
        event["action"]="APPLIED";accepted.append(row);events.append(event)
    return merged,events,accepted,quarantined

def validate(merged):
    """切换前校验：同一身份同版唯一、有效值为最高版本。

    版本号无法解析的行交给 M2 契约层逐行隔离，不阻断整个批次；
    重复版本（同身份同版多行）视为结论不可信，必须阻断。
    """
    problems=[];seen=set()
    for row in merged:
        version=version_of(row)
        if version is None:
            continue
        key=identity(row)
        token=(key,version)
        if token in seen:
            problems.append({"id":row.get("id",""),"batch":key[0],"analyte":key[1],"version":version,"reason":"DUPLICATE_VERSION"})
        seen.add(token)
    return problems

def ledger(rows,original_versions=None,events=None):
    """按身份键输出原版本、当前有效版本与隔离版本，供页面和证据核对。

    同一身份可能来自多个文件（原批次、多次复测），每条来源行都带 source，
    避免不同文件的行号互相冒充。
    """
    original_versions=original_versions or {}
    events=events or []
    groups={}
    for row in rows:
        key=identity(row);version=version_of(row)
        entry=groups.setdefault(key,{"batch":key[0],"analyte":key[1],"id":key[2],"versions":[],"sources":[]})
        source={"source":row.get("source","original"),"line":row.get("line",0),"version":version}
        if source not in entry["sources"]:entry["sources"].append(source)
        if version is not None and version not in entry["versions"]:entry["versions"].append(version)
    quarantined={}
    for event in events:
        if event.get("action") in {"STALE_VERSION","VERSION_CONFLICT","VERSION"}:
            key=(event.get("batch",""),event.get("analyte",""),str(event.get("id","")))
            quarantined.setdefault(key,[]).append({"version":event.get("version",""),"reason":event.get("action"),
                                                   "line":event.get("line",0),"source":event.get("source","retest")})
    out=[]
    for key,entry in groups.items():
        versions=sorted(entry["versions"])
        effective=versions[-1] if versions else None
        original=original_versions.get("|".join(key))
        out.append({"batch":entry["batch"],"analyte":entry["analyte"],"id":entry["id"],
                    "original_version":original,"effective_version":effective,
                    "versions":versions,"sources":sorted(entry["sources"],key=lambda x:(x["source"],x["line"])),
                    "changed":original is not None and effective is not None and effective>original,
                    "quarantined":quarantined.get(key,[])})
    for key,items in quarantined.items():
        if key in groups:continue
        out.append({"batch":key[0],"analyte":key[1],"id":key[2],"original_version":original_versions.get("|".join(key)),
                    "effective_version":None,"versions":[],"sources":[],"changed":False,"quarantined":items})
    return sorted(out,key=lambda x:(x["batch"],x["analyte"],x["id"]))
