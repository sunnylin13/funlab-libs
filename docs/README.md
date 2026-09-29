# funlab-libs docs 索引

| 文件 | 一行說明 |
|---|---|
| [README.md](README.md) | 本索引 |
| [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md) | 改善方案 LIB-01…LIB-18：每項含問題/優先級/完整修正程式碼/測試/驗證指令/風險（供 dev-coder 照做） |
| [DBMGR.md](DBMGR.md) | `DbMgr` 用法與現況限制（**session_context 不可巢狀**） |
| [權限控制開發使用指南.md](權限控制開發使用指南.md) | 路由授權三層守門（default policy／policy_required／豁免）用法與必守規則 |
| [PLUGIN_DEVELOPMENT_GUIDE.md](PLUGIN_DEVELOPMENT_GUIDE.md) | 開發新 plugin：entry point/中繼資料/選單/設定/通知/prewarm/測試模式（逐一核對原始碼） |
| [PLUGIN_LIFECYCLE.md](PLUGIN_LIFECYCLE.md) | Plugin/Manager 狀態機、Layer 1/2/3 擴充層與全部框架內建 hook 名 |
| [PREWARM.md](PREWARM.md) | `funlab.core.prewarm` 現行 API、用法與限制 |

維護規則：只收錄與原始碼一致現行內容；一次性驗證報告/日期式變更紀錄不入 docs
（歷史教訓見 IMPROVEMENT_PLAN LIB-06：pytest 誤收 docs 腳本）。
