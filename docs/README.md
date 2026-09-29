# funlab-libs docs 索引

| 文件 | 一行說明 |
|---|---|
| [README.md](README.md) | 本索引 |
| [DBMGR開發使用指南.md](DBMGR開發使用指南.md) | `DbMgr` 開發用法：session_context 交易語意（巢狀／SAVEPOINT）、生命週期、日誌遮罩 |
| [權限控制開發使用指南.md](權限控制開發使用指南.md) | 路由授權三層守門（default policy／policy_required／豁免）用法與必守規則 |
| [PLUGIN_DEVELOPMENT_GUIDE.md](PLUGIN_DEVELOPMENT_GUIDE.md) | 開發新 plugin：entry point/中繼資料/選單/設定/通知/prewarm/測試模式（逐一核對原始碼） |
| [PLUGIN_LIFECYCLE.md](PLUGIN_LIFECYCLE.md) | Plugin/Manager 狀態機、Layer 1/2/3 擴充層與全部框架內建 hook 名 |
| [PREWARM.md](PREWARM.md) | Prewarm 開發使用指南：範本、規則紅線、API 速查、可觀察性 |

維護規則：只收錄與原始碼一致現行內容；一次性驗證報告／已落地之改善方案不入 docs
（防止 docs 污染 pytest 收集已由 pyproject `testpaths=["tests"]` 隔離）。
