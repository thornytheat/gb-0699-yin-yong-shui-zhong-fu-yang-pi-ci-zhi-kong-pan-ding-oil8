def execute(text):
    from app.pipeline import run
    result=run(text)
    return {"module":7, "report":result["reports"][6], "quarantine":result["quarantine"], "balance":result["balance"]}


def submit(ident,text):
    from app import store
    op=store.submit_retest(ident,text)
    conclusion=op.get("conclusion") or {}
    return {"operation":{"id":op["id"],"status":op["status"],"stage":op["stage"],
                         "conclusion_version":op.get("conclusion_version"),
                         "error":op.get("error")},
            "run_id":ident,
            "current_version":conclusion.get("current_version"),
            "result_version":conclusion.get("result_version"),
            "retest":conclusion.get("result",{}).get("retest"),
            "impact":conclusion.get("result",{}).get("impact"),
            "version_ledger":conclusion.get("result",{}).get("version_ledger"),
            "blocked":conclusion.get("result",{}).get("blocked"),
            "released":conclusion.get("result",{}).get("released")}
