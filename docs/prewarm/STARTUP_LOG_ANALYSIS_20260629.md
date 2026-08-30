# Prewarm 啟動分析與改善執行計畫（2026-06-29）

## 1. 文件目的

本文件針對 2026-06-29 啟動 log 進行事實分析，評估目前 funlab.core.prewarm 機制在現代 Python（3.11+ / 3.13）實務下是否合理，並提出可落地的改善方案與執行計畫。

重點問題：
- 啟動時間過長（建立 App 即 71.65s）。
- 背景 prewarm 任務耗時異常（98s、99s、327s）。
- 目標是降低主線程阻塞，但目前觀察到的總體體感仍偏慢且不穩定。

---

## 2. 先給結論

目前 prewarm 機制「方向正確、實作偏簡」，在中小型系統可用；但在你目前這種多 plugin + 重型依賴 + 外部連線的場景下，已出現以下結構性問題：

1. 啟動瓶頸不只 prewarm，主因先在 plugin 初始化同步成本過高。
2. prewarm 任務沒有全域資源去重，造成同資源重複預熱與互相競爭。
3. prewarm 任務語意不夠嚴格（純 import 與外部 I/O 混用），導致耗時不可預期。
4. 一任務一執行緒模型在重型 import 場景不一定快，可能因 import lock / CPU / I/O 競爭惡化。

所以不是「prewarm 不合理」，而是「prewarm 需要升級為可觀測、可管控、可分流的啟動策略」。

---

## 3. 啟動 log 事實拆解

## 3.1 關鍵時間線（節錄）

- 21:55:42 開始啟動
- 21:56:53 FunlabFlask created（71.65s）
- 21:56:53 觸發 4 個 deferred import
- 21:57:05 Waitress 開始 listen（主線程已進入服務）
- 21:58:34 quotesvcs.calendar 完成（98.946s）
- 21:58:34 finfun_core.twse_calendar 完成（99.219s）
- 22:00:20 fundmgr.scientific_stack 完成（327.062s）

## 3.2 可量化觀察

1. 「App 建立時間」71.65s，發生在 prewarm 之前。
- 代表首要瓶頸在 plugin 載入流程，而非 prewarm 框架本身。

2. 同一類資源（TWSE calendar）似乎被兩個 task 分別預熱。
- quotesvcs.calendar: 98.946s
- finfun_core.twse_calendar: 99.219s
- 兩者完成時間幾乎同步，顯示高度相關或互相等待。

3. scientific_stack 327s 明顯不合理。
- 單純 import pandas/numpy/ffn/scipy 通常不應是數分鐘等級。
- 高機率是「任務內容不純」、「被其他資源鎖住」、「或背景重載競爭」。

4. QuoteService Pool 初始化 132.63s（含券商連線登入）屬外部 I/O。
- 這類工作與 import prewarm 性質不同，應分層治理（連線暖機 vs 模組暖機）。

---

## 4. 對現有 prewarm 設計的評估（現代 Python 觀點）

## 4.1 合理的部分

1. Plugin 擁有任務註冊權（ownership 清晰）是正確方向。
2. 將非關鍵工作放到 background thread，避免阻塞第一個請求路徑，概念正確。
3. 保持 API 簡單（register/run/status）有助長期維護。

## 4.2 不足的部分

1. 缺少「資源級」去重。
- 現在 skip_if_exists 只看 task name，不看實際共享資源。
- 不同 task 名稱可重複做同一件昂貴工作。

2. 缺少任務分類與執行限制。
- import warmup、DB warmup、broker login 混在同一平面。
- 缺少並發上限與 timeout policy。

3. 缺少可觀測欄位。
- 目前 status 只有 status/elapsed/error，無 queue_delay、start_ts、end_ts、owner、resource_key、phase。
- 難以準確回答「慢在排隊、慢在執行、還是慢在互斥等待」。

4. 一任務一 thread 在重型 import 場景風險高。
- import lock 與 CPU/IO 競爭下，盲目增加 thread 不保證更快。

---

## 5. 主要根因假設（依優先級）

1. 重複預熱同一資源（TWSE calendar）造成互斥等待與重工。
2. 部分 prewarm 任務有隱藏副作用（可能觸發 DB、檔案掃描、網路連線）。
3. Plugin 初始化本身過重（SchedService/FundMgrView 於 prewarm 前已花大時間）。
4. 背景任務與排程任務載入同時競爭資源，造成長尾。

---

## 6. 改善策略（建議方案）

## 6.1 短期（低風險，先止血）

1. 實作資源級去重（resource_key）。
- 新增 register 參數 resource_key（可選）。
- 同 resource_key 任務只允許一個執行，其他任務轉為 skipped_shared。

2. 統一 TWSE calendar 的唯一 owner。
- 建議由 finfun_core.twse_calendar 作為唯一預熱任務。
- quotesvcs.calendar 轉為依賴已註冊資源，或僅檢查狀態不重做。

3. 定義 prewarm 任務規範。
- P0（import-only）不得做 network login / 長 DB 掃描。
- 外部連線預熱改列為 P1（service-connect warmup），獨立追蹤。

4. 設定並發上限。
- 預設 max_workers=2 或 3，而不是每 task 一條 thread。
- 先穩定，再追求峰值速度。

## 6.2 中期（可觀測與可治理）

1. 擴充狀態欄位（status v2）。
- owner_plugin, resource_key, category, start_ts, end_ts, queue_delay, run_elapsed。

2. 導入啟動 SLO 與 timeout policy。
- 例如 import-only 任務 budget 30s；超過標記 timeout_warning。
- Python thread 不能強殺，但可紀錄與降級（避免 readiness 被無限拖延）。

3. 建立 readiness 分層。
- liveness: 進程存活。
- readiness_core: 基礎 HTTP/DB 可用。
- readiness_warm: 關鍵 prewarm 完成比例（例如 >= 80%）。

## 6.3 長期（架構優化）

1. 對極重 C-extension / 科學套件考慮離線快取或映像預熱。
- 若部署環境允許，可使用 image build 階段預先建立快取（例如 wheel cache、calendar data cache）。

2. Plugin 初始化做分階段啟動。
- 先讓 HTTP 可服務，再逐步啟動非關鍵 plugin 功能。
- 明確拆分「初始化必要」與「延遲啟用」。

---

## 7. 建議的 prewarm v2 介面（草案）

```python
register(
    name: str,
    func: Callable,
    *,
    blocking: bool = False,
    delay: float = 0.0,
    skip_if_exists: bool = False,
    replace: bool = False,
    resource_key: str | None = None,   # 新增：共享資源去重
    category: str = "import",          # import | service_connect | cache_build
    budget_sec: float | None = None,   # 新增：SLO 觀測
    owner: str | None = None,          # 新增：可觀測
) -> None
```

狀態範例：

```json
{
  "finfun_core.twse_calendar": {
    "status": "done",
    "category": "import",
    "resource_key": "twse_calendar",
    "queue_delay": 0.8,
    "run_elapsed": 21.4,
    "budget_sec": 30,
    "budget_exceeded": false
  }
}
```

---

## 8. 分階段執行計畫（可直接排程）

## Phase 0（1-2 天）基線量測與觀測補齊

目標：先把慢點量化，不憑感覺優化。

1. 在 prewarm 任務加上 start_ts/end_ts/queue_delay/category/resource_key。
2. 輸出結構化 log（JSON line 或固定 key-value）。
3. 建立每次啟動報表：
- App 建立耗時
- Waitress 可服務時間
- 每 task 排隊與執行耗時
- 95th/99th 長尾任務

驗收：同一版本重啟 5 次，統計結果可重現。

## Phase 1（1-2 天）去重與止血

目標：消除重工與明顯不合理耗時。

1. 導入 resource_key 去重。
2. 合併/收斂 TWSE calendar 預熱為單一 owner。
3. 將 scientific_stack 任務內容檢查為純 import，不做任何 DB/網路工作。

驗收：
- 不再同時出現兩個 calendar 類任務各跑 ~99s。
- scientific_stack 耗時顯著下降（目標 < 30s，優先先降級到可接受範圍）。

## Phase 2（2-4 天）並發模型優化

目標：避免 thread 過量競爭與長尾放大。

1. run() 改為 bounded executor（max_workers 可配置）。
2. 依 category 套用不同 worker 池（至少 import 與 connect 分池）。
3. 任務超 budget 時記錄 warning 並標記 degraded。

驗收：
- prewarm 長尾方差下降。
- 啟動 5 次的 P95 變異收斂。

## Phase 3（3-5 天）Plugin 啟動流程瘦身

目標：降低 prewarm 前 71.65s 的同步成本。

1. 逐一盤點 Plugin class 載入成本（特別是 SchedService、FundMgrView）。
2. 將非必要初始化移至 hook 後台或首次使用時延遲。
3. 保持主線程只做「可服務最小集合」。

驗收：
- App created 時間目標降到 < 20s（第一階段）
- 中期目標 < 10-15s（依環境調整）

---

## 9. KPI 與驗收門檻（建議）

建議以同機同環境連續重啟 5 次取中位數 + P95：

1. App created（主初始化）
- 目前：71.65s
- 目標一期：< 20s

2. Waitress ready
- 目前：約 83s（從 21:55:42 到 21:57:05）
- 目標一期：< 30s

3. 單一 prewarm 任務耗時
- 目前最慢：327s
- 目標一期：< 60s
- 目標二期：import-only 任務 P95 < 20s

4. 重複資源暖機
- 目前：存在（calendar）
- 目標：0 件

---

## 10. 風險與注意事項

1. 過度延遲初始化可能把成本轉嫁到第一個特定功能請求。
- 需搭配 readiness_warm 或功能級 fallback。

2. 加入 timeout 不等於可以中斷 thread。
- Python thread 無法安全強制終止，timeout 應作為觀測與降級策略。

3. broker 登入屬外部系統風險。
- 網路、憑證、券商 API 狀態會造成高波動，應與 import prewarm 分開統計。

---

## 11. 建議先做的三件事（最小可行）

1. 先做 resource_key 去重，立刻解決重複 calendar 預熱。
2. 將 prewarm 任務分為 import 與 service_connect 兩類，分池執行。
3. 先把啟動報表固定化（每次開機都能看到同格式 KPI）。

這三件完成後，再進行 plugin 初始化瘦身，成效最明顯且風險可控。

---

## 12. 附錄：本次 log 對應到的程式位置

- prewarm 核心：funlab/core/prewarm.py
- app 啟動 prewarm 呼叫點：funlab/core/appbase.py
- QuoteService prewarm 註冊：finfun-quotesvcs/finfun/quotesvcs/service.py
- FundMgrView prewarm 註冊：finfun-fundmgr/finfun/fundmgr/view.py
- SchedService 背景載入：funlab-sched/funlab/sched/service.py
