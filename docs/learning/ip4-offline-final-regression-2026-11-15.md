# IP-4 最终离线回归状态审计（2026-11-15）

## 已确认

- `python -m pytest -q -p no:cacheprovider tests/test_ip4_v13_offline_audit.py`：3 passed，0.78s，exit 0。
- `python -m pytest -q -p no:cacheprovider tests/test_ip4_v13_blind_payload_contract.py`：2 passed、22 subtests passed，0.06s，exit 0。
- `python -m pytest -vv -x -p no:cacheprovider --maxfail=1 --collect-only`：213 tests collected，1.19s，exit 0。此为收集，不是执行。

## 未通过或未完成

- 全套 `python -m pytest -q -p no:cacheprovider --basetemp=.pytest-final-offline`：约 40 秒无输出后中断，exit 1；原因未知，不得记为通过。
- 较早跨文件安全子集运行输出 15 个点后，工具未提供 pytest 汇总或退出码；结果未知，不计入通过数。
- 本轮没有执行全量前 20 项；因此没有定位卡点用例。没有触网、启动 Docker 或调用模型。

已有 pytest 临时目录未访问或修改。以上结果仅记录可核验的命令输出。
