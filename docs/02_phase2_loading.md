# 02_phase2_loading：推薦引擎 Orchestration（`recommend.py`）逐段拆解教學

本文件是 Phase 2 的教學文件：我們會聚焦在 `src/tripscore/recommender/recommend.py` 這個「推薦引擎的編排器（orchestrator）」。

你可以把它想成「把整個資料流串起來的總控」：它不負責寫很複雜的演算法細節，而是負責把 ingestion / features / scoring 的結果組合起來，輸出可解釋的 Top N 推薦。

---

## 1) 本階段目標（中文）

完成本階段後，你應該能做到：

1. 讀懂推薦引擎的資料流：**輸入偏好 → 載入候選點 → 抓外部資料 → 算 component 分數 → 合成總分 → 排名輸出**
2. 知道為什麼要拆成「ingestion / features / scoring / recommender」
3. 理解「可解釋性」不是口號：它是由 `ScoreBreakdown`、`ScoreComponent.reasons/details` 支撐
4. 理解為什麼要做：
   - timezone normalization
   - weight normalization
   - fail-open（外部資料失效仍回傳結果）
   - dependency injection（測試可注入 stub，不打網路）

---

## 2) 本階段改了哪些檔案？為何要這樣拆？（中文）

### ✅ 改動檔案

- `src/tripscore/recommender/recommend.py`
  - 加入大量「初學者可讀」的英文註解（解釋資料流與每一步的原因）
  - 小幅重構（不改功能）：
    - 把 component weights 的 precedence logic（config → preset → request）抽成 `_effective_component_weights(...)`
    - 讓 client 建立使用 lazy cache（只有需要時才建 cache/clients），以支援測試注入

### 為什麼 Phase 2 選這個檔案？

因為對初學者來說，「推薦系統」最容易迷失在細節（到底從哪裡開始？資料怎麼流？）。  
`recommend.py` 是最適合當「學習入口」的檔案：你看懂它，就知道整個系統如何運作。

---

## 3) 核心概念講解（中文，含中英對照）

### 3.1 Orchestration（編排）是什麼？

**Orchestration（編排）** 指的是：把多個模組「依正確順序」串起來，負責控制資料流與依賴關係。

在本專案中：

- `ingestion/*`：負責「抓資料」（外部 API），可能失敗，需要 cache
- `features/*`：負責「把原始資料變成 0..1 分數」+ reasons/details（可解釋）
- `scoring/*`：負責「數學工具」例如 clamp/normalize（保持結果穩定）
- `recommender/recommend.py`：負責「把上面全部串起來」並輸出 Top N

> 中英對照：orchestrator（編排器）/ pipeline（管線）/ dependency（依賴）

### 3.2 Dependency Injection（依賴注入）為什麼重要？

如果 `recommend()` 內部「直接 new 一個 TdxClient + WeatherClient」：

- 測試會打網路（不穩定、慢、不可重現）
- CI 上會因為沒有 credentials 或網路限制而失敗

所以我們設計成：

- `recommend(..., tdx_client=StubTdxClient(), weather_client=StubWeatherClient())`

這樣測試只測「資料流與排序邏輯」，不測外部 API。

> 中英對照：dependency injection（依賴注入）/ stub（替身）/ deterministic（可重現）

### 3.3 Fail-open（失敗時仍產生結果）

外部資料（TDX、天氣）不一定永遠可用：

- 沒有 credentials
- API 失效
- rate limit

如果外部資料一壞就整個 500，你的產品體驗會很差。  
因此我們採用 fail-open：

- 抓不到資料時，用「neutral score」或「只用能算的訊號」
- 同時把錯誤寫入 `details` / `reasons`（讓使用者知道：為什麼這次的分數比較不準）

> 中英對照：fail-open（失敗仍繼續）/ graceful degradation（優雅降級）

### 3.4 Normalize Weights（權重正規化）是為什麼？

我們的 component weights 來自：

1) config defaults  
2) preset  
3) user override  

但使用者可能輸入一個不加總為 1 的數列，例如：0.35 / 0.30 / 0.20 / 0.15（剛好是 1）或 10 / 5 / 0 / 0（不是 1）。

為了保持：

- contribution 可比較
- total_score 的語意穩定（約略落在 0..1）

我們把 weights normalize 成 sum = 1.0。

> 中英對照：normalize（正規化）/ contribution（貢獻值）

---

## 4) 程式碼分區塊貼上（對應 `src/tripscore/recommender/recommend.py`）

### 4.1 模組定位（Module header）

```python
# This module is the "orchestrator" for the recommendation pipeline.
# It wires together:
# - domain input (UserPreferences)
# - ingestion (TDX + weather clients)
# - feature scoring (accessibility, weather, preference, context)
# - final ranking + explainable breakdown (RecommendationResult)
```

#### 你要理解的重點（中文）

這段不是寫爽的註解，它在宣告「設計邊界」：

- 這個檔案不是用來放一堆 scoring 公式
- 公式應該在 `features/*`
- 這裡只做 orchestration：把資料搬運到正確的地方、把結果組合回來

---

### 4.2 `build_cache(settings)`：為什麼要有 cache？

```python
def build_cache(settings: Settings) -> FileCache:
    cache_dir = Path(settings.cache.dir)
    return FileCache(
        cache_dir,
        enabled=settings.cache.enabled,
        default_ttl_seconds=settings.cache.default_ttl_seconds,
    )
```

#### 逐段解釋（中文）

1. `cache_dir = Path(settings.cache.dir)`
   - 把字串路徑轉成 `Path`，避免 Windows/Linux 分隔符差異
2. `FileCache(...)`
   - 讓 ingestion client（TDX/Weather）可以把結果寫到磁碟，避免重複打 API
   - 對產品面：更快、更穩定、更省 API 次數
   - 對工程面：更可重現（同一個 cache key → 同一份資料）

常見延伸：

- 真正上線後可能會把 FileCache 換成 Redis/DB，但介面保持一致即可

---

### 4.3 `_effective_component_weights(...)`：權重 precedence 與 normalize

```python
def _effective_component_weights(
    preferences: UserPreferences,
    settings: Settings,
    *,
    preset_component_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    weights = dict(settings.scoring.composite_weights)
    if preset_component_weights:
        weights.update(preset_component_weights)
    if preferences.component_weights:
        override = preferences.component_weights.model_dump(exclude_none=True)
        weights.update(override)
    return normalize_weights(weights)
```

#### 逐段解釋（中文）

這段是 Phase 2 的重點之一：把「權重怎麼決定」的規則集中起來。

- `weights = dict(settings.scoring.composite_weights)`
  - 先從 config 起手，確保 key 齊全（accessibility/weather/preference/context）
- `preset_component_weights`（如果有）
  - preset 是「產品預設意圖」：例如 rainy_day_indoor 強調 weather
- `preferences.component_weights`（如果有）
  - user override 永遠優先：因為使用者手動調的就是這一次的需求
- `normalize_weights(weights)`
  - 把總和變成 1，讓 contribution 可比較、總分落在穩定區間

可能的坑：

- 如果 preset 裡寫錯 key（例如拼錯），normalize 會把那個未知 key 也算進總和  
  → 會稀釋四大 component 的權重，導致總分偏低  
  → 未來可以考慮加上「unknown key warning」的 log（但這是後續 phase 的事）

---

### 4.4 `recommend(...)` 主流程：從輸入到輸出

下面是 `recommend()` 的資料流核心（我們分段看）。

#### (A) Step 1：載入 settings + 套用 per-request overrides

```python
settings = settings or get_settings()
settings = apply_settings_overrides(settings, preferences.settings_overrides)
```

重點：

- `get_settings()` 會讀 YAML + env overrides，回傳一個 Pydantic `Settings`
- `apply_settings_overrides(...)` 只影響本次推薦，不改動全域設定

#### (B) Step 2：Client 建立（支援測試注入）

```python
cache: FileCache | None = None
if tdx_client is None:
    if cache is None:
        cache = build_cache(settings)
    tdx_client = TdxClient(settings, cache)
if weather_client is None:
    if cache is None:
        cache = build_cache(settings)
    weather_client = WeatherClient(settings, cache)
```

重點：

- 只有在「沒有注入 client」時才建立真實 client
- 測試可以塞 stub，讓 pytest 不打網路
- `cache` 會被兩個 client 共用（減少 IO）

#### (C) Step 3–7：Preset、時間窗、effective query normalization

```python
start = ensure_tz(preferences.time_window.start, settings.app.timezone)
end = ensure_tz(preferences.time_window.end, settings.app.timezone)
effective_weights = _effective_component_weights(...)
normalized_query = preferences.model_copy(update={...})
```

重點：

- `ensure_tz`：避免 naive datetime（沒有時區）造成比較錯誤
- `normalized_query`：回傳給前端/CLI，讓使用者知道「本次系統實際用了哪些參數」

#### (D) Step 8–10：載入 catalog → 篩選候選 → 取得外部訊號

```python
if destinations is None:
    destinations = load_destinations(Path(settings.catalog.path))

candidates = [d for d in destinations if _passes_tag_filters(...)]

try:
    bus_stops = tdx_client.get_bus_stops()
except Exception as e:
    ...
```

重點：

- 先做 tag filter：因為這是「最便宜」的運算（不用打 API）
- 外部資料全部採用 try/except：fail-open

#### (E) Step 11：逐目的地計算 component 分數 + 組合 ScoreBreakdown

```python
for dest in candidates:
    metrics = compute_accessibility_metrics(...)
    a_score, a_details, a_reasons = score_accessibility(...)
    summary = weather_client.get_summary(...)
    w_score, w_details, w_reasons = score_weather(...)
    p_score, p_details, p_reasons = score_preference_match(...)
    c_score, c_details, c_reasons = score_context(...)
    components = [ScoreComponent(...), ...]
    total_score = clamp01(sum(c.contribution for c in components))
```

重點：

- 每個 component 都回傳：`score (0..1) + details + reasons`
- `ScoreBreakdown` 是「可解釋性」的核心輸出
- `clamp01` 避免分數超界導致 UI/排序不穩

---

## 5) 常見錯誤與排查（中文）

### 5.1 速度很慢（尤其 destinations 很多）

原因：

- 目前 weather 會對每個 destination 呼叫一次（雖然有 cache）

排查/改善方向：

1. 先確認 `.cache/tripscore/` 是否有寫入（cache 是否啟用）
2. 未來可做：
   - 批次請求或網格化（同區域共用天氣）
   - 先做地理篩選（離 origin 太遠的先剔除）

### 5.2 TDX 全部不可用

原因常見：

- `.env` 沒設 `TDX_CLIENT_ID` / `TDX_CLIENT_SECRET`

結果：

- accessibility 會退化成「只看 origin distance + neutral transit」

你可以在結果的 `details` / `reasons` 看到類似：

- `"TDX bus stop data unavailable"`

### 5.3 時間窗怪怪的（例如跨時區）

注意：

- `ensure_tz` **只會補上缺少的時區**，不會把 +00:00 轉成 Asia/Taipei

排查：

- 確認你送進來的 start/end 是否一致使用 +08:00（或 UI 有沒有正確附上）

---

## 6) 本階段驗收方式（中文 + 英文命令）

### 6.1 Run tests（should be fully offline）

```bash
PYTHONPATH=src pytest -q
```

預期：

- 所有測試通過
- 不會打外部網路（tests 使用 stub client）

### 6.2 Run API + Web

```bash
PYTHONPATH=src uvicorn tripscore.api.app:app --reload --port 8000
```

預期：

- 啟動無錯誤
- 打開 `http://127.0.0.1:8000/` 後，按 Run 能拿到推薦結果

---

## 7) 下一步（先預告，不在本 Phase 做）

下一個 Phase 很適合做：

1. 把 `features/accessibility.py` 拆解成更易讀的段落（並補上教學文件）
2. 或者把 Web editor 的 `settings_overrides` JSON 規格做更完整的 schema/驗證提示（但要小心不要擴大 Phase）

