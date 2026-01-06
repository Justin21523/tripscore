# 00_overview：TripScore 專案教學總覽

這份文件的目標是用「初學者也能看懂」的方式，快速建立你對 TripScore 專案的全局理解：這個系統在解決什麼問題？資料怎麼流動？每個資料夾負責什麼？以及你該從哪裡開始讀程式碼。

---

## 1) TripScore 是什麼？（問題 → 解法）

TripScore 是一個「可解釋（explainable）」的旅遊目的地推薦系統：

- 使用者提供：出發地點（origin）、可用時間窗（time window）、偏好（tags/weights）
- 系統整合：交通可達性訊號（TDX）、天氣預報、地區因子（例如人潮、親子友善）
- 產出：Top N 目的地 + 每個目的地的分數拆解與理由（reasons）

最重要的設計決策：

1. **第一版採用規則/加權評分（Rule-based, Weighted Scoring）**：先可解釋，再談機器學習。
2. **所有權重/門檻走 config**：避免 hard-code，方便迭代與 A/B 調參。
3. **模組化資料管線**：`ingestion → features → scoring → recommender → api → web`

---

## 2) 資料流（Data Flow）長什麼樣？

你可以把 TripScore 想成一個「資料管線」：

1. **API/Web/CLI** 收到使用者偏好（`UserPreferences`）
2. **Recommender** 負責 orchestration：載入景點、呼叫資料擷取、計算各項 feature、合成分數、排序
3. **Ingestion** 只做「取資料」：例如 TDX、天氣 API（必要時使用 cache）
4. **Features** 把原始資料變成可比較的 0..1 分數（Accessibility / Weather / Preference / Context）
5. **Scoring** 把多個分數用權重合成（CompositeScore），同時附上理由（Explain）
6. **輸出** `RecommendationResult`，讓前端渲染列表、地圖、Inspector

一個最重要的「新手觀念」：

> ingestion 不應該偷偷做 scoring；features/scoring 也不應該直接打外部 API。  
> 這種責任切分能讓程式更好測、更好改，也更容易 debug。

---

## 3) 專案目錄結構（你應該去哪裡找什麼）

以下是最常用的路徑（只列出初學者最需要知道的）：

```text
src/tripscore/
  api/            # FastAPI routes + app entry
  catalog/        # Destination catalog loader
  cli.py          # CLI demo entry
  config/         # YAML defaults + settings loader (+ overrides)
  domain/         # Pydantic data models (request/response schema)
  features/       # Score components (0..1) + reasons
  ingestion/      # External data clients (TDX, weather)
  recommender/    # Orchestration + ranking
  scoring/        # Utilities: normalize, clamp, explain helpers
  web/            # Minimal web UI (template + JS + CSS)

data/
  catalogs/       # Destination catalog JSON
  factors/        # District baseline factors

tests/            # pytest tests (offline stubs + smoke tests)
docs/             # 中文教學文件（你正在讀的這裡）
```

---

## 4) 你該從哪裡開始讀程式碼？

建議閱讀順序（由「最像產品入口」到「核心引擎」）：

1. `src/tripscore/api/app.py`：Web 入口（會把 `/` 指到 template）
2. `src/tripscore/api/routes.py`：`POST /api/recommendations` 實際在呼叫 recommender
3. `src/tripscore/recommender/recommend.py`：整個排名流程的核心（pipeline orchestration）
4. `src/tripscore/features/*`：每個 component score 怎麼算（可解釋的理由在哪裡生成）
5. `src/tripscore/ingestion/*`：外部資料怎麼抓、怎麼 cache
6. `src/tripscore/domain/models.py`：API 的 request/response schema（Pydantic）

---

## 5) 新手術語小抄（中英對照）

- **ingestion（資料擷取）**：從外部系統拉資料回來（例如 API）。
- **feature（特徵）**：把原始資料轉成可比較的數值（常見是 0..1 分數）。
- **scoring（評分）**：把多個 feature 合成總分（加權、規則、門檻）。
- **explainability（可解釋性）**：每個分數/排名必須能回答「為什麼」。
- **orchestration（編排）**：負責把各模組串起來，而不是把所有邏輯塞在同一個檔案。

---

## 6) 如何跑起來（簡版）

更詳細的啟動、驗收方式請看 `docs/01_phase1_bootstrap.md`。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run tests (offline)
PYTHONPATH=src pytest -q

# Run API + Web (open http://127.0.0.1:8000/)
PYTHONPATH=src uvicorn tripscore.api.app:app --reload --port 8000
```

---

## 7) 常見問題與排查方向（先看這裡）

1. **TDX 抓不到資料**：請先確認 `.env` 的 `TDX_CLIENT_ID` / `TDX_CLIENT_SECRET` 是否存在；沒有也能跑，但會退化使用「中性分數/距離」。
2. **地圖不顯示**：Leaflet 圖磚需要網路（OpenStreetMap tiles）。即使沒有地圖，列表與評分仍可使用。
3. **結果看起來怪**：先把 component weights 設成單一維度（例如只看 Weather），確認該分數邏輯是否符合直覺，再逐步加回其他 component。

