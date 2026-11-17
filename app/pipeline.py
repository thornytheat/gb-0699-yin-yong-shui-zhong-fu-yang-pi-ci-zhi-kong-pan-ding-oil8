from pathlib import Path
from app.report import plain,reason_counts,report
from app.modules.m1_samples.rules import apply as stage1
from app.modules.m2_contract.rules import apply as stage2
from app.modules.m3_parallel.rules import apply as stage3
from app.modules.m4_spikes.rules import apply as stage4
from app.modules.m5_blanks.rules import apply as stage5
from app.modules.m6_release.rules import apply as stage6
from app.modules.m7_retest.rules import apply as stage7

STAGES=(stage2,stage3,stage4,stage5,stage6,stage7)

def sample_text():
    return (Path(__file__).parent.parent/"sample_data"/"instrument.csv").read_text(encoding="utf-8-sig")

def retest_text():
    return (Path(__file__).parent.parent/"sample_data"/"retest.csv").read_text(encoding="utf-8-sig")

def run(text):
    ctx={"text":text,"quarantine":[],"reports":[]}
    ctx["reports"].append(stage1(ctx))
    return evaluate(ctx)

def prepare(text):
    """只执行受理（M1），供增量复测取得原始行与隔离证据。"""
    ctx={"text":text,"quarantine":[],"reports":[]}
    ctx["reports"].append(stage1(ctx))
    ctx.pop("text",None)
    return ctx
def evaluate(ctx):
    """对已合并的受理行重新执行 M2—M7。

    ctx 需含 raw、quarantine、input_count、blank_rows；可选 intake_quarantine、
    original_versions、affected（受影响的质控键）。quarantine 只放受理层隔离，
    业务层隔离由 M2—M7 在合并行上确定性重算，避免多版本累加重复。
    """
    ctx.setdefault("quarantine",[])
    if not ctx.get("reports"):
        ctx["reports"]=[report(ctx["raw"],样品=len(ctx["raw"]),隔离=len(ctx["quarantine"]))]
    for stage in STAGES:
        ctx["reports"].append(stage(ctx))
    return _finish(ctx)

def _finish(ctx):
    ctx.pop("text",None)
    ctx["reasons"]=reason_counts(ctx)
    if "intake_quarantine" in ctx:
        ctx["intake_quarantine"]=[dict(x) for x in ctx["intake_quarantine"]]
    return plain(ctx)
