import sqlite3, json, hashlib
from datetime import datetime, timezone
from app.config import DATA_DIR, RULE_VERSION
from app.errors import BusinessError

ORIGINAL_VERSION=0

def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"runs.sqlite", timeout=15)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, decision TEXT NOT NULL DEFAULT '待复核', created TEXT NOT NULL)")
    columns={row["name"] for row in db.execute("PRAGMA table_info(runs)")}
    if "current_version" not in columns:
        db.execute("ALTER TABLE runs ADD COLUMN current_version INTEGER NOT NULL DEFAULT 0")
    db.execute("CREATE TABLE IF NOT EXISTS conclusions (run_id TEXT NOT NULL, version INTEGER NOT NULL, op_id TEXT, result TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(run_id,version))")
    db.execute("CREATE TABLE IF NOT EXISTS retest_ops (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, text_digest TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, prepared TEXT, computed TEXT, error TEXT, conclusion_version INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL, UNIQUE(run_id,text_digest))")
    db.execute("CREATE TABLE IF NOT EXISTS reviews (run_id TEXT NOT NULL, result_version INTEGER NOT NULL, revision INTEGER NOT NULL, decision TEXT NOT NULL, created TEXT NOT NULL)")
    return db

def _now():
    return datetime.now(timezone.utc).isoformat()

def _digest(text):
    return hashlib.sha256((RULE_VERSION+"\n"+text).encode()).hexdigest()

def save(text, result):
    digest=_digest(text)
    ident=digest[:16]
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,digest,result,created) VALUES(?,?,?,?)",(ident,digest,json.dumps(dict(result,rule_version=RULE_VERSION),ensure_ascii=False),_now()))
    return get(ident)

def _row(ident):
    with connect() as db:
        row=db.execute("SELECT * FROM runs WHERE id=?",(ident,)).fetchone()
    if row is None: raise BusinessError("NOT_FOUND","批次不存在",404)
    return row

def _conclusion(db,ident,version):
    return db.execute("SELECT * FROM conclusions WHERE run_id=? AND version=?",(ident,version)).fetchone()

def get(ident, version=None):
    """返回结论；默认当前版本。version=0 返回原批次受理时的原始结论。"""
    row=_row(ident)
    with connect() as db:
        current=row["current_version"] or ORIGINAL_VERSION
        wanted=current if version is None else version
        stored=_conclusion(db,ident,wanted) if wanted!=ORIGINAL_VERSION else None
        max_version=db.execute("SELECT COALESCE(MAX(version),0) FROM conclusions WHERE run_id=?",(ident,)).fetchone()[0]
    if wanted<ORIGINAL_VERSION or wanted>max(ORIGINAL_VERSION,max_version):
        raise BusinessError("VERSION_NOT_FOUND","结论版本不存在",404)
    if stored is not None:
        result=json.loads(stored["result"])
    elif wanted==ORIGINAL_VERSION:
        result=json.loads(row["result"])
    else:
        raise BusinessError("VERSION_NOT_FOUND","结论版本不存在",404)
    item=dict(row)
    item.pop("result",None)
    item["result"]=result
    item["result_version"]=wanted
    item["current_version"]=current
    item["original_version"]=ORIGINAL_VERSION
    item["rule_version"]=result.get("rule_version","2026.11.18.1")
    return item

def original(ident):
    return get(ident,ORIGINAL_VERSION)

def history():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id,revision,decision,created,current_version FROM runs ORDER BY created DESC LIMIT 40")]

def review(ident,revision,decision):
    if not isinstance(decision,str) or decision not in {"通过","退回"}: raise BusinessError("DECISION","复核决定无效")
    current=get(ident)
    if decision=="通过" and current["result"].get("blocked"):
        raise BusinessError("BLOCKED","存在阻断项，请先修正数据",409)
    with connect() as db:
        changed=db.execute("UPDATE runs SET revision=revision+1,decision=? WHERE id=? AND revision=?",(decision,ident,revision)).rowcount
        if not changed: raise BusinessError("STALE","版本已更新，请刷新",409)
        db.execute("INSERT INTO reviews(run_id,result_version,revision,decision,created) VALUES(?,?,?,?,?)",
                   (ident,current["current_version"],revision+1,decision,_now()))
    return get(ident)

def review_history(ident,version=None):
    """复核决定台账：切换结论会重置当前待复核，但历史决定仍可查。"""
    origin=get(ident,ORIGINAL_VERSION)
    with connect() as db:
        rows=[dict(r) for r in db.execute("SELECT result_version,revision,decision,created FROM reviews WHERE run_id=? ORDER BY revision",(ident,))]
    if version is not None:rows=[r for r in rows if r["result_version"]==version]
    return [{"result_version":0,"revision":0,"decision":"待复核","created":origin["created"]}]+rows

# ---------- 增量复测 ----------

def _op_id(ident,text):
    return hashlib.sha256((ident+"\n"+_digest(text)).encode()).hexdigest()[:24]

def _op_view(db,op):
    item=dict(op)
    for field in ("prepared","computed"):
        item[field]=json.loads(item[field]) if item.get(field) else None
    item["conclusion"]=get(item["run_id"])
    return item

def _load_op(db,op_id):
    return db.execute("SELECT * FROM retest_ops WHERE id=?",(op_id,)).fetchone()

def _mark(db,op_id,status,stage,error=None,prepared=None,computed=None,conclusion_version=None):
    db.execute("UPDATE retest_ops SET status=?,stage=?,error=?,updated=? WHERE id=?",
               (status,stage,error,_now(),op_id))
    if prepared is not None:
        db.execute("UPDATE retest_ops SET prepared=? WHERE id=?",(json.dumps(prepared,ensure_ascii=False),op_id))
    if computed is not None:
        db.execute("UPDATE retest_ops SET computed=? WHERE id=?",(json.dumps(computed,ensure_ascii=False),op_id))
    if conclusion_version is not None:
        db.execute("UPDATE retest_ops SET conclusion_version=? WHERE id=?",(conclusion_version,op_id))

def submit_retest(ident,text):
    """受理增量复测；同一批次同一文本为同一操作，重试不新增版本。

    进度分阶段提交：受理 -> 校验并重算（持久化中间结果） -> 切换结论。
    中断后再次提交会从最后确认的阶段继续。
    """
    if not isinstance(text,str): raise BusinessError("TEXT","输入须为文本")
    _row(ident)
    op_id=_op_id(ident,text)
    with connect() as db:
        op=_load_op(db,op_id)
        if op is None:
            # 并发重试同一复测：UNIQUE(run_id,text_digest) 兜底，后到者复用已有操作。
            db.execute("INSERT OR IGNORE INTO retest_ops(id,run_id,text_digest,text,status,stage,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                       (op_id,ident,_digest(text),text,"受理中","ACCEPTED",_now(),_now()))
            op=_load_op(db,op_id)
        if op["status"]=="已完成":
            return _op_view(db,op)
    return _advance(op_id)

def _advance(op_id):
    from app import retest
    with connect() as db:
        op=_load_op(db,op_id)
    if op is None: raise BusinessError("NOT_FOUND","复测操作不存在",404)
    if op["status"]=="已完成":
        with connect() as db: return _op_view(db,_load_op(db,op_id))
    if not op["prepared"]:
        try:
            prepared=retest.prepare(op["run_id"],op["text"])
        except BusinessError as exc:
            with connect() as db: _mark(db,op_id,"校验失败","PREPARED",error=f"{exc.code}: {exc}")
            raise
        with connect() as db:
            _mark(db,op_id,"已计算","PREPARED",prepared=prepared)
    return _switch(op_id)


def _reset(db,op_id,error):
    """切换失败：保留旧版结论，清除中间结果，使重试从受理阶段重新开始。"""
    db.execute("UPDATE retest_ops SET status='受理中',stage='ACCEPTED',prepared=NULL,computed=NULL,error=?,updated=? WHERE id=?",
               (error,_now(),op_id))


def _switch(op_id):
    from app import retest
    with connect() as db:
        op=_load_op(db,op_id)
    prepared=json.loads(op["prepared"])
    base=prepared["base_version"]
    if prepared.get("accepted",0)==0 and prepared.get("quarantined",0)==0:
        # 纯重放：同一版本同一内容，不新增结论版本。
        with connect() as db:
            _mark(db,op_id,"已完成","REPLAY",error=None,conclusion_version=base)
            return _op_view(db,_load_op(db,op_id))
    # 若上次中断已算出结果，接着最后确认的处理进度。
    computed=json.loads(op["computed"]) if op["computed"] else None
    if computed is None:
        try:
            # 切换前再次完整重算并校验，确保结论基于当前原批次全部历史。
            computed=retest.compute(prepared)
        except BusinessError as exc:
            with connect() as db: _mark(db,op_id,"校验失败","PREPARED",error=f"{exc.code}: {exc}")
            raise
        with connect() as db:
            _mark(db,op_id,"已计算","COMPUTED",computed=computed)
    db=connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        run=db.execute("SELECT current_version FROM runs WHERE id=?",(op["run_id"],)).fetchone()
        if run is None:
            db.rollback();raise BusinessError("NOT_FOUND","批次不存在",404)
        existing=db.execute("SELECT op_id FROM conclusions WHERE run_id=? AND version=?",(op["run_id"],base+1)).fetchone()
        if existing is not None:
            if existing["op_id"]!=op_id:
                db.rollback()
                with connect() as other:
                    _reset(other,op_id,"目标结论版本已被占用，保留旧版结论")
                raise BusinessError("STALE","结论版本已被占用，保留旧版结论",409)
            # 上次已写入结论但未推进指针，恢复最后步骤。
            db.execute("UPDATE runs SET current_version=?,decision='待复核',revision=revision+1 WHERE id=?",(base+1,op["run_id"]))
            db.execute("UPDATE retest_ops SET status='已完成',stage='SWITCHED',conclusion_version=?,updated=? WHERE id=?",(base+1,_now(),op_id))
            db.commit()
            return _op_view(db,_load_op(db,op_id))
        if run["current_version"]!=base:
            db.rollback()
            with connect() as other:
                _reset(other,op_id,"当前结论已更新，保留旧版结论")
            raise BusinessError("STALE","当前结论已更新，保留旧版结论",409)
        stored=json.dumps(dict(computed,rule_version=RULE_VERSION,
                               conclusion={**computed.get("conclusion",{}),"switched":True}),ensure_ascii=False)
        db.execute("INSERT INTO conclusions(run_id,version,op_id,result,created) VALUES(?,?,?,?,?)",(op["run_id"],base+1,op_id,stored,_now()))
        db.execute("UPDATE runs SET current_version=?,decision='待复核',revision=revision+1 WHERE id=? AND current_version=?",(base+1,op["run_id"],base))
        if db.execute("SELECT changes()").fetchone()[0]==0:
            db.rollback()
            with connect() as other:
                _reset(other,op_id,"当前结论已更新，保留旧版结论")
            raise BusinessError("STALE","当前结论已更新，保留旧版结论",409)
        db.execute("UPDATE retest_ops SET status='已完成',stage='SWITCHED',error=NULL,conclusion_version=?,updated=? WHERE id=?",(base+1,_now(),op_id))
        db.commit()
    except Exception as exc:
        try: db.rollback()
        except Exception: pass
        if not isinstance(exc,BusinessError):
            with connect() as other: _mark(other,op_id,"切换失败","COMPUTED",error=str(exc))
            raise BusinessError("SWITCH","切换失败，保留旧版结论",409)
        raise
    finally:
        db.close()
    with connect() as db: return _op_view(db,_load_op(db,op_id))

def retest_ops(ident):
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id,run_id,status,stage,conclusion_version,created,updated FROM retest_ops WHERE run_id=? ORDER BY created",(ident,))]
