# 证据读取恢复 v1

现有数学研究页的GET读取使用同一串行队列。服务端原WorkerBusy返回503及JSON字段code=WORKER_BUSY、retryable_read=true、中文提示，并附Retry-After: 1。POST遇到该异常也返回503，但retryable_read=false，不自动重发。

明确暂忙的GET最多8次请求、7次重读；间隔250／750／1500／3000／5000／5000／5000毫秒，共20.5秒主动等待。每次重新读取完整当前报告，不使用旧E4缓存。排队和HTTP审计时间另计。超过上限提示手动刷新；其他未分类503保持最多3次请求，其他错误不重读。

切换研究项目／模式／图语境后，尚未发送和等待重读的旧GET丢弃；已在途的旧响应也丢弃。不会取消已经执行的服务端数学操作，不自动发POST。等待期间旧原图判断隐藏、旧执行确认清除；只有原审计成功返回才显示当前状态。姓名仍是本地声明，数学门禁、来源撤回、完整类型／公理及独立重放要求保持。

传输诊断snapshot包含requests、read_retries、busy_retries、discarded_reads、pending、running；它们是当前页面请求计数，不是数学成绩或历史累计。详见[ADR0062](../adr/0062-bounded-evidence-read-recovery.md)。
