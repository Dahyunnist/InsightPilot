# InsightPilot 快速启动与复现

## 1. 准备环境

Python 3.11+。在项目根目录创建虚拟环境、安装锁定依赖，并配置本机 `.env`：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.env` 填入 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。API 要求鉴权时，再配置 `INSIGHTPILOT_API_TOKEN`；令牌默认具有 `refund:read,refund:cancel` scope。不要提交 `.env`。

## 2. 构建合成数据库与冻结 gold

```powershell
.\.venv\Scripts\python.exe -m data.enterprise_seed
.\.venv\Scripts\python.exe -m data.build_enterprise_v4
```

第二条命令重建 30 道题的独立 Python gold 和 manifest。运行前检查 manifest 的问题、gold 和数据指纹；题目文件改动会导致冻结哈希变化。

## 3. 运行同题规则基线和真实模型

```powershell
.\.venv\Scripts\python.exe -m insightpilot.current_benchmark --mode baseline
.\.venv\Scripts\python.exe -m insightpilot.current_benchmark --mode model
```

模型臂会逐题调用当前 `.env` 模型；guard 直接拦截的问题不会产生模型请求。原始轨迹和汇总分别写入 `reports/current-benchmark/baseline-raw.jsonl`、`model-raw.jsonl`、`baseline-summary.json`、`model-summary.json`。汇总按实际 HTTP 请求次数、响应 token、缓存 token、模型时延和 DeepSeek 官方费率估算成本。账户抵扣与实扣金额不可从响应 token 推知。

## 4. 运行 API 演示

终端 A：

```powershell
.\.venv\Scripts\python.exe -m insightpilot.api_server
```

终端 B：

```powershell
$headers = @{ "Content-Type" = "application/json" }
if ($env:INSIGHTPILOT_API_TOKEN) { $headers.Authorization = "Bearer $env:INSIGHTPILOT_API_TOKEN" }
$body = @{ question = "2026-10-01 到 2026-10-31 华东批准退款金额率是多少？" } | ConvertTo-Json -Compress
$result = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/analysis -Headers $headers -Body $body
$result | ConvertTo-Json -Depth 12
```

响应含 `run_id`、结构化计划、参数化 SQL、结果和独立复核。用 `GET /runs/{run_id}` 查看持久运行记录。服务校验失败返回 `422`，令牌无效返回 `401`，scope 不足返回 `403`，并提供 `X-Request-ID`。

## 5. 运行 MCP 演示与回归

终端 C 启动 MCP Streamable HTTP 服务：

```powershell
.\.venv\Scripts\python.exe -m insightpilot.mcp_server
```

将 MCP 客户端连接到 `http://127.0.0.1:8001/mcp`，列出 `analyze_refund` 工具，并读取 `metrics://refund-catalog` 与 `docs://product-contract` 资源。测试覆盖 in-process 与 HTTP transport。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

此合成集用于本地回归和演示，不代表真实客户总体、生产负载或 SLA。
