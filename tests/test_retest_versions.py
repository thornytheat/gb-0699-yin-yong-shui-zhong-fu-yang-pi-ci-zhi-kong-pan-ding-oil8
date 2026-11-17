import os
import json
import pytest
os.environ["DATA_DIR"]="/tmp/water-lab-pytest"
from app import store, retest, versions, scope, changes, pipeline
from app.pipeline import run, sample_text, retest_text
from app.errors import BusinessError
from app.exporter import evidence_csv


def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(store,"DATA_DIR",tmp_path)
    base=store.save(sample_text(),run(sample_text()))
    return base


def test_retest_applies_higher_versions_only(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    op=store.submit_retest(base["id"],retest_text())
    assert op["status"]=="已完成" and op["stage"]=="SWITCHED"
    current=store.get(base["id"])
    assert current["current_version"]==1
    assert current["result_version"]==1
    events={ (e["id"],e["version"]):e["action"] for e in current["result"]["retest"]["events"] }
    assert events[("006S","3")]=="APPLIED"
    assert events[("010S","1")]=="VERSION_CONFLICT"


def test_original_evidence_survives_switch(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    original=store.get(base["id"],0)
    before_parallel=[dict(r) for r in original["result"]["parallel"]]
    before_blanks=[dict(r) for r in original["result"]["blank_checks"]]
    before_release=[dict(r) for r in original["result"]["released"]]
    store.submit_retest(base["id"],retest_text())
    still=store.get(base["id"],0)
    assert still["result"]["parallel"]==before_parallel
    assert still["result"]["blank_checks"]==before_blanks
    assert still["result"]["released"]==before_release
    current=store.get(base["id"])
    assert current["result"]["original"]["released"]==before_release
    assert current["result"]["original"]["blank_checks"]==before_blanks


def test_retest_rejudges_only_affected_checks(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    store.submit_retest(base["id"],retest_text())
    impact=store.get(base["id"])["result"]["impact"]
    changed={(r["label"],r["scope"]) for r in impact if r["changed"]}
    assert ("平行样","P6") in changed
    assert ("空白","010B") in changed
    assert ("批次放行","") in changed
    untouched=[r for r in impact if r["scope"] in {"P1","P2","P3","P4","P5"}]
    assert untouched==[]


def test_retest_retry_same_text_does_not_add_version(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    first=store.submit_retest(base["id"],retest_text())
    second=store.submit_retest(base["id"],retest_text())
    assert first["id"]==second["id"]
    assert store.get(base["id"])["current_version"]==1
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM conclusions WHERE run_id=?",(base["id"],)).fetchone()[0]==1


def test_retest_resumes_from_last_confirmed_stage(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    prepared=retest.prepare(base["id"],retest_text())
    op_id=store._op_id(base["id"],retest_text())
    with store.connect() as db:
        db.execute("INSERT INTO retest_ops(id,run_id,text_digest,text,status,stage,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                   (op_id,base["id"],store._digest(retest_text()),retest_text(),"受理中","ACCEPTED","x","x"))
        store._mark(db,op_id,"已计算","PREPARED",prepared=prepared)
    assert store.get(base["id"])["current_version"]==0
    op=store.submit_retest(base["id"],retest_text())
    assert op["status"]=="已完成" and op["stage"]=="SWITCHED"
    with store.connect() as db:
        rows=[dict(r) for r in db.execute("SELECT version,op_id FROM conclusions WHERE run_id=?",(base["id"],))]
    assert rows==[{"version":1,"op_id":op_id}]


def test_switch_failure_keeps_old_conclusion(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    prepared=retest.prepare(base["id"],retest_text())
    op_id=store._op_id(base["id"],retest_text())
    with store.connect() as db:
        db.execute("INSERT INTO retest_ops(id,run_id,text_digest,text,status,stage,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                   (op_id,base["id"],store._digest(retest_text()),retest_text(),"受理中","ACCEPTED","x","x"))
        store._mark(db,op_id,"已计算","PREPARED",prepared=prepared)
        db.execute("INSERT INTO conclusions(run_id,version,op_id,result,created) VALUES(?,?,?,?,?)",(base["id"],1,"other-op","{}","x"))
    with pytest.raises(BusinessError) as caught:
        store.submit_retest(base["id"],retest_text())
    assert caught.value.status==409
    current=store.get(base["id"])
    assert current["current_version"]==0
    assert current["result_version"]==0
    with store.connect() as db:
        db.execute("DELETE FROM conclusions WHERE run_id=? AND version=1",(base["id"],))
    op=store.submit_retest(base["id"],retest_text())
    assert op["stage"]=="SWITCHED"
    assert store.get(base["id"])["current_version"]==1


def test_late_old_version_cannot_roll_back(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    v3="id,batch,analyte,kind,pair,value,spike,unit,version\n008S,B02,Pb,sample,P8,0.012,0,mg/L,3\n"
    store.submit_retest(base["id"],v3)
    late="id,batch,analyte,kind,pair,value,spike,unit,version\n008S,B02,Pb,sample,P8,0.009,0,mg/L,2\n"
    store.submit_retest(base["id"],late)
    current=store.get(base["id"])
    effective={(r["id"],r["rev"],r["value"]) for r in current["result"]["effective"] if r["id"]=="008S"}
    assert effective=={("008S",3,"0.012")}
    assert any(q["reason"]=="STALE_VERSION" for q in current["result"]["quarantine"])
    entries=[x for x in current["result"]["version_ledger"] if x["id"]=="008S"]
    assert entries and entries[0]["effective_version"]==3
    assert {"reason":"STALE_VERSION"} in [{k:v for k,v in q.items() if k in {"reason"}} for q in entries[0]["quarantined"]]


def test_same_version_different_value_isolated(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    before=store.get(base["id"])
    conflict="id,batch,analyte,kind,pair,value,spike,unit,version\n006S,B02,Pb,sample,P6,0.099,0,mg/L,1\n"
    op=store.submit_retest(base["id"],conflict)
    current=store.get(base["id"])
    assert op["status"]=="已完成"
    assert any(q["reason"]=="VERSION_CONFLICT" for q in current["result"]["intake_quarantine"])
    # 同版异值被隔离，有效值不采用冲突值。
    assert {(r["id"],r["rev"],r["value"]) for r in current["result"]["effective"]}==\
           {(r["id"],r["rev"],r["value"]) for r in before["result"]["effective"]}


def test_same_version_same_value_replay_is_idempotent(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    replay="id,batch,analyte,kind,pair,value,spike,unit,version\n006S,B02,Pb,sample,P6,0.010,0,mg/L,1\n"
    before=store.get(base["id"])
    op=store.submit_retest(base["id"],replay)
    after=store.get(base["id"])
    assert op["stage"]=="REPLAY"
    assert after["current_version"]==0
    assert before["result"]["effective"]==after["result"]["effective"]


def test_evidence_has_original_and_current(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    store.submit_retest(base["id"],retest_text())
    text=evidence_csv(store.get(base["id"]))
    assert "original_release" in text and "previous_release" in text
    assert "version_ledger" not in text
    assert "impact" in text
    assert base["id"] in text


def test_conclusion_version_not_found(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    with pytest.raises(BusinessError) as caught:
        store.get(base["id"],5)
    assert caught.value.status==404


def test_api_view_original_and_current(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(store,"DATA_DIR",tmp_path)
    client=TestClient(app,raise_server_exceptions=False)
    ident=client.post("/api/run",json={"text":sample_text()}).json()["id"]
    assert client.post("/api/7/retest",json={"id":ident,"text":retest_text()}).status_code==200
    current=client.get(f"/api/runs/{ident}").json()
    original=client.get(f"/api/runs/{ident}?version=0").json()
    assert current["result_version"]==1 and original["result_version"]==0
    assert current["result"]["previous"]["released"]==original["result"]["released"]
    page=client.get(f"/module/6?run_id={ident}")
    assert page.status_code==200 and "原批次 v0" in page.text
    assert client.get(f"/module/6?run_id={ident}&version=0").status_code==200
    export=client.get(f"/api/export/{ident}").text
    assert "original_release" in export and "impact" in export
    assert client.get(f"/api/runs/{ident}/versions").json()["current_version"]==1


def test_version_text_normalization_same_value(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    # 同版本不同写法（01 与 1）且同值：视为重放，不新增版本。
    replay="id,batch,analyte,kind,pair,value,spike,unit,version\n006S,B02,Pb,sample,P6,0.010,0,mg/L,01\n"
    op=store.submit_retest(base["id"],replay)
    assert op["stage"]=="REPLAY"
    assert store.get(base["id"])["current_version"]==0
    assert not any(q["reason"]=="VERSION_CONFLICT" and q.get("id")=="006S"
                   for q in store.get(base["id"])["result"]["intake_quarantine"])


def test_new_identity_accepted_on_retest(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    text=("id,batch,analyte,kind,pair,value,spike,unit,version\n"
          "011S,B02,Pb,sample,P11,0.010,0,mg/L,2\n"
          "011D,B02,Pb,duplicate,P11,0.0105,0,mg/L,2\n"
          "011A,B02,Pb,spike,P11,0.020,.01,mg/L,2\n"
          "011B,B02,Pb,blank,,.0002,0,mg/L,2\n")
    op=store.submit_retest(base["id"],text)
    current=store.get(base["id"])
    assert op["stage"]=="SWITCHED"
    assert any(r["pair"]=="P11" for r in current["result"]["parallel"])
    assert any(r["id"]=="011B" for r in current["result"]["blank_checks"])


def test_unparseable_version_does_not_block(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    text="id,batch,analyte,kind,pair,value,spike,unit,version\n012S,B02,Pb,sample,P12,0.010,0,mg/L,abc\n"
    op=store.submit_retest(base["id"],text)
    current=store.get(base["id"])
    assert op["stage"]=="SWITCHED"
    assert any(q["reason"]=="VERSION" and q.get("id")=="012S" for q in current["result"]["quarantine"])


def test_balance_holds_after_retest(tmp_path,monkeypatch):
    base=fresh(tmp_path,monkeypatch)
    store.submit_retest(base["id"],retest_text())
    balance=store.get(base["id"])["result"]["balance"]
    assert balance["input"]==balance["accepted"]+balance["quarantined"]+balance["blank"]


def test_review_history_survives_switch(tmp_path,monkeypatch):
    monkeypatch.setattr(store,"DATA_DIR",tmp_path)
    text=("id,batch,analyte,kind,pair,value,spike,unit,version\n"
          "001S,B09,Pb,sample,P1,0.010,0,mg/L,1\n"
          "001D,B09,Pb,duplicate,P1,0.0105,0,mg/L,1\n"
          "001A,B09,Pb,spike,P1,0.020,.01,mg/L,1\n"
          "001B,B09,Pb,blank,,.0002,0,mg/L,1\n")
    base=store.save(text,run(text))
    store.review(base["id"],base["revision"],"通过")
    retest="id,batch,analyte,kind,pair,value,spike,unit,version\n001S,B09,Pb,sample,P1,0.0101,0,mg/L,2\n"
    store.submit_retest(base["id"],retest)
    current=store.get(base["id"])
    assert current["decision"]=="待复核" and current["current_version"]==1
    history=store.review_history(base["id"],0)
    assert any(r["decision"]=="通过" for r in history)
