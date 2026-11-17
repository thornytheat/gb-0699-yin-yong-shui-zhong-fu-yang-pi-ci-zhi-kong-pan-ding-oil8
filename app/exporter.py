import csv,io,json

def _row(writer,run,kind,line,reason,evidence):
    writer.writerow([run["id"],run["digest"],run["rule_version"],kind,line,reason,json.dumps(evidence,ensure_ascii=False)])

def evidence_csv(run):
    """唯一证据CSV：含运行摘要、当前结论、原批次证据、版本台账与隔离记录。

    当前结论与原版本同时列出，便于核对复测是否只改动受影响的质控项。
    """
    result=run["result"]
    out=io.StringIO();writer=csv.writer(out)
    writer.writerow(["run_id","sha256","rule_version","kind","line","reason","evidence"])
    _row(writer,run,"manifest","","",{
        "balance":result.get("balance"),
        "decision":run["decision"],
        "revision":run["revision"],
        "result_version":run.get("result_version"),
        "current_version":run.get("current_version"),
        "original_version":0,
        "conclusion":result.get("conclusion"),
        "rule_version":run["rule_version"]})
    for row in result.get("final",[]):
        _row(writer,run,"result",row.get("line",""),row.get("status",""),row)
    for row in result.get("released",[]):
        _row(writer,run,"release","",row.get("status",""),row)
    for row in result.get("parallel",[]):
        _row(writer,run,"parallel","",row.get("status",""),row)
    for row in result.get("recoveries",[]):
        _row(writer,run,"recovery",row.get("line",""),row.get("status",""),row)
    for row in result.get("blank_checks",[]):
        _row(writer,run,"blank",row.get("line",""),row.get("status",""),row)
    if "original" in result:
        _row(writer,run,"original_release","","",{"version":0,"released":result["original"].get("released",[]),
                                                 "final":result["original"].get("final",[]),
                                                 "balance":result["original"].get("balance",{})})
    if "previous" in result:
        _row(writer,run,"previous_release","","",{"version":result["previous"].get("version"),
                                                  "released":result["previous"].get("released",[]),
                                                  "final":result["previous"].get("final",[])})
    for row in result.get("version_ledger",[]):
        lines=";".join(f"{s.get('source','original')}:{s.get('line','')}:v{s.get('version','')}" for s in row.get("sources",[]))
        _row(writer,run,"version",lines,"CHANGED" if row.get("changed") else "",row)
    for row in result.get("impact",[]):
        _row(writer,run,"impact","",row.get("after",""),row)
    for row in result.get("retest",{}).get("events",[]):
        _row(writer,run,"retest_event",row.get("line",""),row.get("action",""),row)
    for row in run.get("reviews",[]):
        _row(writer,run,"review",str(row.get("revision","")),row.get("decision",""),row)
    for row in result.get("quarantine",[]):
        _row(writer,run,"quarantine",row.get("line",""),row["reason"],row)
    return "﻿"+out.getvalue()
