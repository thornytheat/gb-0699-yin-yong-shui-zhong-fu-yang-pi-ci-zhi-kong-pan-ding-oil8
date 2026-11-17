from app.errors import object_payload, BusinessError
from fastapi import APIRouter, Body
from app.modules.m7_retest.service import execute, submit
router=APIRouter()

@router.post("/api/7")
def calculate(payload:object=Body(default=None)):
    payload=object_payload(payload)
    from app.pipeline import sample_text
    text=payload.get("text",sample_text())
    if not isinstance(text,str):
        raise BusinessError("TEXT","输入须为文本")
    return execute(text)

@router.get("/api/7")
def sample():
    from app.pipeline import sample_text
    return execute(sample_text())

@router.post("/api/7/retest")
def retest(payload:object=Body(default=None)):
    """从原批次受理带版本号的增量复测；同一批次同一内容重试不新增版本。"""
    payload=object_payload(payload)
    ident=payload.get("id")
    if not isinstance(ident,str) or not ident:
        raise BusinessError("RETEST","批次编号须为文本")
    text=payload.get("text")
    if text is None:
        from app.pipeline import retest_text
        text=retest_text()
    if not isinstance(text,str):
        raise BusinessError("TEXT","输入须为文本")
    return submit(ident,text)

@router.get("/api/7/retests/{ident}")
def retests(ident:str):
    from app import store
    store.get(ident)
    return {"operations":store.retest_ops(ident)}

@router.get("/api/7/sample-retest")
def sample_retest():
    from app.pipeline import retest_text
    return {"text":retest_text()}
