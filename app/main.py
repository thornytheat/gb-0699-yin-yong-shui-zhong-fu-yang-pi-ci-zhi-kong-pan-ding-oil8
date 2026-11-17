from fastapi import FastAPI, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.errors import BusinessError, object_payload
from app.pipeline import run, sample_text
from app import store
from app.exporter import evidence_csv
from app.numbers import display_number
from app import changes
from pathlib import Path
import importlib
app=FastAPI(title='饮用水重复样与批次质控判定')
app.mount("/static",StaticFiles(directory=Path(__file__).parent/"static"),name="static")
templates=Jinja2Templates(directory=Path(__file__).parent/"templates")
templates.env.filters["display_number"]=display_number
MODULES=['样品接收', '检测项契约', '平行样核对', '加标回收', '空白污染', '批次放行', '复测追踪']
for n,slug in enumerate(['samples', 'contract', 'parallel', 'spikes', 'blanks', 'release', 'retest'],1):
    app.include_router(importlib.import_module(f"app.modules.m{n}_{slug}.handler").router)

def _conclusion_version(request):
    raw=request.query_params.get("version")
    if raw is None or raw=="":return None
    try:
        value=int(raw)
    except ValueError:
        raise BusinessError("VERSION","结论版本须为整数")
    if value<0:raise BusinessError("VERSION","结论版本不能为负")
    return value

def _run(request):
    ident=request.query_params.get("run_id")
    version=_conclusion_version(request)
    if ident:
        return store.get(ident,version)
    item=store.save(sample_text(),run(sample_text()))
    return store.get(item["id"],version) if version else item

@app.exception_handler(BusinessError)
async def domain_error(request,exc):
    return JSONResponse({"error":exc.code,"message":str(exc)},status_code=exc.status)
@app.get("/health")
def health(): return {"status":"ok"}
@app.get("/",response_class=HTMLResponse)
def home(request:Request): return page(request,1)
@app.get("/module/{module}",response_class=HTMLResponse)
def page(request:Request,module:int):
    if module not in range(1,8): raise BusinessError("MODULE","模块不存在",404)
    item=_run(request)
    original=store.original(item["id"])
    context={"title":'饮用水重复样与批次质控判定',"modules":MODULES,"active":module,"run":item,
             "report":item["result"]["reports"][module-1],"original":original,
             "reviews":store.review_history(item["id"],item["result_version"]),
             "impact":changes.compare(original["result"],item["result"]) if item["result_version"] else []}
    return templates.TemplateResponse(request=request,name="workbench.html",context=context)
@app.post("/api/run")
def calculate(payload:object=Body(default=None)):
    payload=object_payload(payload)
    text=payload.get("text",sample_text())
    if not isinstance(text,str): raise BusinessError("TEXT","输入须为文本")
    return store.save(text,run(text))
@app.get("/api/runs")
def list_runs(): return store.history()
@app.get("/api/runs/{ident}")
def run_detail(ident:str,version:int|None=None): return store.get(ident,version)
@app.get("/api/runs/{ident}/versions")
def run_versions(ident:str):
    item=store.get(ident)
    with store.connect() as db:
        rows=[dict(r) for r in db.execute("SELECT version,op_id,created FROM conclusions WHERE run_id=? ORDER BY version",(ident,))]
    return {"id":ident,"current_version":item["current_version"],"original_version":0,
            "versions":[{"version":0,"op_id":None,"created":item["created"]}]+rows}

@app.get("/api/runs/{ident}/reviews")
def run_reviews(ident:str,version:int|None=None):
    return {"id":ident,"reviews":store.review_history(ident,version)}
@app.post("/api/review")
def review(payload:object=Body(...)):
    payload=object_payload(payload)
    if not isinstance(payload.get("id"),str) or not isinstance(payload.get("decision"),str): raise BusinessError("REVIEW","复核编号与决定须为文本")
    if type(payload.get("revision")) is not int: raise BusinessError("REVISION","版本须为整数")
    return store.review(str(payload.get("id","")),payload["revision"],payload.get("decision"))
@app.get("/api/export/{ident}")
def export(ident:str,version:int|None=None):
    item=store.get(ident,version)
    item["reviews"]=store.review_history(ident,item["result_version"])
    return Response(evidence_csv(item),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=evidence.csv"})
@app.get("/api/sample")
def sample(): return {"text":sample_text()}
