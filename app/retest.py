"""增量复测编排。

prepare：以当前结论为合并基线，把增量行按身份键与版本裁决追加为新版本。
         原批次受理时的原始证据（平行样、空白、放行）另行留存，不随重算消失。
compute：在合并基线上重跑全部质控（M2—M7），得到新结论、版本台账与质控重判影响。
         只有全部重算与版本校验完成，调用方才允许原子切换当前结论。

对账口径：输入行数 = 合并后的受理行 + 受理层隔离 + 空行；重放（同版同值）不进入台账，
重试同一复测不新增版本。业务层隔离每次在合并行上确定性重算，不累加。
"""
from app import pipeline, versions, scope, changes
from app.errors import BusinessError
from app.config import RULE_VERSION

EVIDENCE_FIELDS=("raw","effective","parallel","recoveries","blank_checks","released","final")


def _evidence(result, label, with_quarantine=False):
    missing=[f for f in EVIDENCE_FIELDS if f not in result]
    if missing:
        raise BusinessError("BASELINE",f"{label}缺少证据，无法增量复测")
    evidence={f:result[f] for f in EVIDENCE_FIELDS}
    if with_quarantine:
        evidence["quarantine"]=result.get("quarantine",[])
    evidence["balance"]=result.get("balance",{})
    return evidence


def _intake_quarantine(result):
    if "intake_quarantine" in result:return [dict(x) for x in result["intake_quarantine"]]
    # 旧版结论未单独保存受理层隔离：按原因码从中恢复，保证证据不丢。
    reasons={"REPEATED_HEADER","COLUMN_COUNT","ID_REQUIRED","VERSION_CONFLICT"}
    return [dict(x) for x in result.get("quarantine",[]) if x.get("reason") in reasons]


def prepare(ident,text):
    from app import store
    original=store.original(ident)
    current=store.get(ident)
    original_evidence=_evidence(original["result"],"原批次",with_quarantine=True)
    current_evidence=_evidence(current["result"],"当前结论")
    intake=pipeline.prepare(text)
    if not intake.get("raw") and not intake.get("quarantine"):
        raise BusinessError("RETEST_EMPTY","复测没有可用记录")
    merged,events,accepted,quarantined=versions.merge(current["result"]["raw"],intake["raw"])
    original_versions=current["result"].get("original_versions") or original["result"].get("original_versions") or {}
    changed={"|".join(versions.identity(r)) for r in accepted}
    keys=scope.affected_checks(current["result"]["raw"]+intake["raw"],changed)
    intake_q=_intake_quarantine(current["result"])+list(intake.get("intake_quarantine",[]))
    for row,reason in quarantined:
        intake_q.append({"line":row.get("line",0),"id":row.get("id",""),"reason":reason,"raw":dict(row)})
    blank_rows=current["result"].get("blank_rows",current["result"].get("balance",{}).get("blank",0))+intake.get("blank_rows",0)
    return {"run_id":ident,"base_version":current["current_version"],
            "raw":merged,"quarantine":intake_q,"intake_quarantine":intake_q,
            "blank_rows":blank_rows,
            "input_count":len(merged)+len(intake_q)+blank_rows,
            "original_versions":original_versions,
            "events":events,
            "affected":scope.scope_summary(keys),
            "affected_keys":[list(k) for k in sorted(keys,key=str)],
            "accepted":len(accepted),"quarantined":len(quarantined),
            "current_evidence":current_evidence,
            "original_evidence":original_evidence,
            "current_conclusion":{"version":current["current_version"],
                                  "blocked":current["result"].get("blocked"),
                                  "released":current["result"].get("released",[]),
                                  "final":current["result"].get("final",[]),
                                  "balance":current["result"].get("balance",{})}}


def compute(prepared):
    base_version=prepared["base_version"]
    ctx={"raw":prepared["raw"],"quarantine":list(prepared["intake_quarantine"]),
         "intake_quarantine":prepared["intake_quarantine"],
         "blank_rows":prepared["blank_rows"],"input_count":prepared["input_count"],
         "original_versions":prepared["original_versions"]}
    result=pipeline.evaluate(ctx)
    problems=versions.validate(result["raw"])
    if problems:
        raise BusinessError("VERSION_INVALID","重算后版本校验失败，保留旧版结论")
    result["retest"]={"events":prepared["events"],"affected":prepared["affected"],
                      "accepted":prepared["accepted"],"quarantined":prepared["quarantined"]}
    result["version_ledger"]=versions.ledger(result["raw"],prepared["original_versions"],prepared["events"])
    result["impact"]=changes.impact(prepared["current_evidence"],result,
                                    {tuple(k) for k in prepared["affected_keys"]})
    result["original"]={"version":0,**prepared["original_evidence"]}
    result["previous"]={"version":base_version,**prepared["current_conclusion"]}
    result["conclusion"]={"version":base_version+1,"previous_version":base_version,
                          "rule_version":RULE_VERSION,"switched":False}
    return result
