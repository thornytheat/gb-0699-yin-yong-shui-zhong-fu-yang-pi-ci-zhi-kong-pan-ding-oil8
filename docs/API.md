# 接口

`POST /api/run`：{"text":"CSV内容"}。同规则版本与输入摘要只产生一条批次。

`GET/POST /api/1` 至 `/api/7`：独立样例或自定义文件计算。各模块返回 report、quarantine、balance。

`POST /api/7/retest`：{"id":"批次编号","text":"带版本号的增量复测CSV"}。以原批次受理为基线追加新版本，按样品与检测项裁决有效值，重判受影响质控项；全部重算与版本校验通过后切换当前结论。同一批次同一内容重试返回同一操作，不新增版本。处理中断后重试从最后确认阶段继续。缺省 text 载入 `sample_data/retest.csv`。

`GET /api/7/retests/{id}`：该批次的复测操作进度。`GET /api/7/sample-retest`：复测样例。

`GET /api/runs/{id}`：当前结论。`GET /api/runs/{id}?version=0`：原批次受理时的只读结论。`GET /api/runs/{id}/versions`：结论版本列表。`GET /api/runs/{id}/reviews`：复核决定台账；`?version=0` 只看原批次。

`POST /api/review`：id、整数revision、decision取通过或退回。旧版本409；阻断项不能通过。切换结论会推进 revision 并回到待复核，历史决定保留在复核台账。

`GET /api/export/{id}`：唯一证据CSV，含当前结论、原批次放行、上一版结论、版本台账、重判影响与隔离记录；`?version=0` 导出原批次证据。`GET /health`：存活检查。

错误：结构/数值422，版本冲突或切换失败409（保留旧版结论），不存在404，文件过大413。逐行异常进入quarantine；晚到旧版与同版异值隔离为 STALE_VERSION、VERSION_CONFLICT。
