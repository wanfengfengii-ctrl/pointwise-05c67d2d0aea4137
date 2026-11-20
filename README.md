# 带材测振站班段归档

白班采集员从有限容量的本地记录器回收测振原码。质检员按班段签发逐槽结果，夜班凭成果号在停源后复领。记录器会复用物理格，覆写前已受理的读取仍可能交回对应原槽。采集员可以在等待旧槽时继续回收后槽。

## 运行

需要 Python 3.11 和标准库。项目物化后，从根目录运行完整回归和工作日样例。

```sh
python3 -B check_project.py
python3 -B station.py demo data/workday.json
python3 -B station.py demo
python3 -B rescue_fixture.py
```

样例使用独立临时站库，关闭来源后重新打开站库，并显示签出及复领的实际结果。
`station.py demo`（不带样例文件）演示定额抢救：重叠班段的重选、逐步发读、成报与
停源重开后的原报复领。带样例文件时在同一份输出里附带 `rescue` 结果。`check_project.py`
运行全部原回归并核验抢救重选、原票续办与签报交接，业务断言失败时返回非零。

## 定额抢救

记录器持续覆写时，按一次工单的回读额度尽量补齐未签班段。详细语义见 `RESCUE.md`。

- `register_rescue(job_id, recording_id, weights, budget)` 登记工单。`weights` 为
  班段号到正整数权重的字典，`budget` 为非负整数（布尔值不算整数）。每个录制只有
  一个工单；同参重投返回原工单当前状态，异参、错绑和非法输入抛 `ValueError`，
  未知工单、录制或班段抛 `KeyError`。
- `advance_rescue(job_id, connection)` 推进一次：按已接纳原样和当前设备可读槽重算，
  在剩余额度内选总权重最大的可补齐班段集合，每步最多发一笔读，前笔未结不发。
- `rescue_status(job_id)` 查询，不发起回读。三个入口返回同一字典：`used`、
  `remaining`、`pending`（未结原 `ReadTicket` 或 `None`）、`targets`、
  `completed`（均为 `set[str]`）。

设备实际受理才扣一次额度；明确失败不退，未受理不扣，普通读不扣。设备已受理而站库
写入或提交失败时，重开站库并接回同一在线设备与连接后先认回原票及占额，即使原槽已
覆写。连接终止作废旧票但保留已扣额度。覆盖未决槽的班段在无实收时保持待结，`issue`
仍返回原 `PendingIssue`；结清后按原规则签发，停源后仍可用 `retrieve` 按成果号复领。

## 业务接口

- `Station(path, LocalDevice(capacity, first_slot))` 打开站库并接入本地设备。`Station(path)` 只打开站库。用 `with` 或 `close()` 关闭数据库句柄。关闭句柄和终止设备连接是两个独立事件。
- `open_shift(shift_id, recording_id, zero, numerator, denominator)` 登记开班并在接入设备上建立新录制。标识为非空白字符串，零点及比例分子分母为整数，分母不得为零。每份录制绑定一个班次。既有登记同参重提返回原绑定，冲突抛出 `ValueError`。接续同一录制时保留原 `LocalDevice`，新设备的录制使用新录制号。
- `define_section(section_id, shift_id, first_slot, last_slot)` 固定班段闭区间。槽号是非负整数。未知班次抛出 `KeyError`，反向区间或同号异参抛出 `ValueError`。同参重提返回原定义。
- `record(recording_id, raw)` 写一个整数原码并返回原槽号。未知录制抛出 `KeyError`，结束后写入抛出 `ValueError`。`end_recording(recording_id)` 停止写槽，当前仍驻留的格在设备在线时可读。
- `start(recording_id)` 建立该录制的设备连接。`acquire(connection, recording_id, slot)` 在活动连接上受理读票据。无法新读时返回已持久原样（若有）或 `SlotAvailability`。错归属或无效连接抛出 `ValueError`。`find_observation(recording_id, slot)` 查询已接纳原样，缺项返回 `None`。
- `queue_reply(ticket, succeeded=True, delay=0)` 排入设备回交，然后用 `device.events.run_next()` 取出 `ReadResult`。`complete_reply(result)` 核对并提交。明确失败只结清该票据。错归属返回 `Completion.REJECTED`，失效连接的回交返回 `Completion.STALE`，已结清的回交重送返回 `Completion.DUPLICATE`。`prepare_reply(result)` 可先核对并暂存待提交回交，之后传给 `complete_reply` 重新核对。`complete_reply` 可接受 `acknowledge` 回调，成功或失败的处理结果在事务结束后排入设备事件队列。
- `terminate(connection)` 结束连接并持久作废未结清的旧票据。`reopen(connection)` 在原录制上为已结束连接建立新代次。重开失败可重试。已经提交的原样及首份出处依然可用。
- `issue(section_id)` 对未签班段返回 `PendingIssue` 或已经提交的 `IssuedReport`。待结结果列出尚未确定的原槽。未知班段抛出 `KeyError`，存储错误向调用方返回异常。已签班段重提返回同一成果及首执。
- `retrieve(number)` 按成果号返回固定 `IssuedReport`，未知号返回 `None`。停源后可以通过该接口复领，也可用原班段号重提已签成果。

站端和本地设备由调用者在同一串行事件序列中驱动。设备读受理与站库登记之间不会运行回交。提交前暂停不持有数据库事务，恢复时重新核对资格。站库初始化补齐表和索引，保留原登记、原样和成果。设备缓冲区及排队回交由 `LocalDevice` 持有，站库保存可复领的原样、连接裁决和报告。单纯断源、尚未写入或来源不可查询时不能据此判定缺测。

## 班报口径

逐槽按原槽号升序排列。已持久接纳的原样有效，包含原码和原出处。没有原样且已证明不可补齐的槽列为缺测。有效读值为 `(原码 - 冻结零点) × 冻结有理比例` μm。逐槽值及合计使用 `Fraction`，有效零计一条，全缺测时计数和合计均为零。首份接纳的出处记录录制号、原槽、物理格、连接代次和设备请求号。报告保存固定数值和出处，与首执共同提交。
