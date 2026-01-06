# Phase 4 — Weather Feature（天氣特徵）教學

## 1) 本階段目標（中文）

這個 Phase 的目標是讓你能「完全看懂」TripScore 的 **天氣特徵（Weather feature）** 如何把天氣資料轉成 **0..1 的可解釋分數**，並且知道要去哪裡調參數。

你會學到：

1. `ingestion/weather_client.py` 提供的 `WeatherSummary` 是什麼（以及為什麼會有 `None`）。
2. `features/weather.py` 如何把「降雨機率、溫度」轉成 `rain_score`、`temperature_score`（0..1）。
3. 「權重（weights）」如何決定雨跟溫度的相對重要性，並且怎麼做 **再正規化（renormalization）**。
4. 為什麼要做 **fail-open（失敗開放）**：天氣抓不到也要能推薦，並在理由中說明。
5. 室內/室外 tag 如何用最小規則做出「避雨偏好」的 proxy（rain importance multiplier）。

---

## 2) 本階段改了哪些檔案？為什麼（中文）

本 Phase 僅處理一個模組（符合「一次一個 Phase / 一個模組」）：

- `src/tripscore/features/weather.py`
  - 加入逐行（或小區塊）英文註解，解釋每個步驟的 **why + what** 與常見坑（missing data、權重錯誤、單位）。
  - 保留既有功能與輸出 shape（score、details、reasons 的 keys 不變），避免破壞既有 API / UI。

新增教學文件：

- `docs/04_phase4_weather.md`（本文件）

---

## 3) 核心概念講解（中英對照）

你會在這個模組看到以下概念：

- **WeatherSummary（天氣摘要）**：ingestion 層把一段時間窗的逐小時預報聚合後的結果。
  - `max_precipitation_probability`：時間窗內「最大降雨機率」（0..100，可能為 `None`）
  - `mean_temperature_c`：時間窗內「平均溫度」（°C，可能為 `None`）
- **Feature score（特徵分數）**：把原始值轉成 0..1，方便跟其他模組一起加權。
- **Comfort window（舒適區間）**：`[t_min, t_max]` 內溫度給滿分，超出就扣分。
- **Penalty scale（扣分尺度）**：超出舒適區間多少度，會被扣到 0 分的速度（線性）。
- **Weights（權重）**：雨 vs 溫度的相對重要性（預設由 config 給，使用者也能覆寫）。
- **Renormalization（再正規化）**：因為我們可能調整雨的權重（乘上 multiplier），所以最後要除以 `w_rain + w_temp` 讓分數仍落在可控範圍。
- **Fail-open（失敗開放）**：外部 API 失敗、資料缺漏時，回 `neutral_score`，並把原因寫在 `reasons`。

---

## 4) Weather feature 在整體資料流的位置（中文）

在 `src/tripscore/recommender/recommend.py` 中，每個目的地都會：

1. 呼叫 `weather_client.get_summary(...)` 拿到 `WeatherSummary`
2. 呼叫 `features/weather.py::score_weather(...)` 得到：
   - `score`：0..1
   - `details`：結構化細節（給 UI/Debug panel）
   - `reasons`：可讀原因（給 CLI/UI 列表）
3. 最終在 composite scoring 依權重混合到總分

因此 `features/weather.py` 的工作，是「把 ingestion 的 raw 值變成產品可以用的 explainable signal」。

---

## 5) 程式碼分區塊引用 + 逐段解釋（中文）

### 5.1 函式入口：`score_weather(summary, destination, preferences, settings)`

檔案：`src/tripscore/features/weather.py`（節錄）

```py
def score_weather(
    summary: WeatherSummary, *, destination: Destination, preferences: UserPreferences, settings: Settings
) -> tuple[float, dict, list[str]]:
    cfg = settings.ingestion.weather
```

你要知道的事情：

- `summary` 是 ingestion 的輸出，可能帶 `None`（例如 API 失敗、缺欄位、或時間窗超出預報範圍）。
- `destination.tags` 會影響「雨的重要性 multiplier」（室內 vs 室外）。
- `preferences.weather_rain_importance` 可以覆寫預設權重，代表使用者覺得雨有多重要（0..1）。
- `settings` 讓所有門檻/權重集中管理在 YAML（避免硬編碼）。

---

### 5.2 Step 1：雨的子分數 `rain_score`

節錄：

```py
if summary.max_precipitation_probability is None:
    rain_score = float(settings.scoring.neutral_score)
else:
    rain_score = 1 - clamp01(float(summary.max_precipitation_probability) / 100.0)
```

拆解：

- `max_precipitation_probability` 是 0..100（百分比）。
- 我們用最直觀的線性轉換：
  - 0% → `rain_score = 1.0`（非常乾爽）
  - 100% → `rain_score = 0.0`（非常可能下雨）
- 如果資料是 `None`：代表「不知道」，我們就回 `neutral_score`（例如 0.5）。
  - 這就是 **fail-open**：天氣掛了也要能跑推薦（產品上更可靠）。

---

### 5.3 Step 2：溫度的子分數 `temperature_score`（舒適區間 + 線性扣分）

節錄：

```py
t = float(summary.mean_temperature_c)
t_min = float(cfg.comfort_temperature_c.min)
t_max = float(cfg.comfort_temperature_c.max)

if t_min <= t <= t_max:
    temp_score = 1.0
else:
    distance = (t_min - t) if t < t_min else (t - t_max)
    temp_score = 1 - clamp01(distance / max(float(cfg.temperature_penalty_scale_c), 0.1))
```

拆解：

1) `t_min` / `t_max` 是「你覺得舒服的溫度區間」。例如 22~28°C。  
2) 在舒適區間內：直接給滿分 1.0。  
3) 超出舒適區間：用距離 `distance` 做線性扣分。
4) `temperature_penalty_scale_c` 決定扣分速度：
   - scale 越小 → 超出一點就扣很快（比較挑剔）
   - scale 越大 → 扣分比較慢（比較寬容）
5) `max(..., 0.1)` 是保護：避免 scale 被設成 0 造成除以 0。

---

### 5.4 Step 3：決定雨 vs 溫度的權重（使用者可覆寫）

節錄：

```py
if preferences.weather_rain_importance is not None:
    w_rain_base = float(preferences.weather_rain_importance)
    w_temp_base = 1.0 - w_rain_base
else:
    w_rain_base = float(cfg.score_weights.rain)
    w_temp_base = float(cfg.score_weights.temperature)
```

拆解：

- 使用者如果提供 `weather_rain_importance`（例如 0.8）：
  - `w_rain_base = 0.8`（雨非常重要）
  - `w_temp_base = 0.2`（溫度相對不重要）
- 如果使用者沒提供，就用 config 的預設值：
  - `ingestion.weather.score_weights.rain`
  - `ingestion.weather.score_weights.temperature`

為什麼「使用者覆寫」用 `1 - w_rain`，但「預設值」用 config 兩個欄位？

- 使用者 UI 通常只提供「雨重要性」一個旋鈕比較直覺。
- 產品預設值則可能想要更彈性（例如兩者不一定要先加總 = 1，最後由 denom 再正規化）。

---

### 5.5 Step 4：室內/室外 tag 調整雨的重要性（multiplier）

節錄：

```py
multiplier = 1.0
multipliers = settings.features.weather.rain_importance_multiplier
is_indoor = "indoor" in destination.tags
is_outdoor = "outdoor" in destination.tags

if is_indoor and not is_outdoor:
    multiplier = float(multipliers.get("indoor", 1.0))
elif is_outdoor and not is_indoor:
    multiplier = float(multipliers.get("outdoor", 1.0))
```

拆解：

- 這是 MVP 的「最小可用規則」：用 tag 代表情境，不上 ML 也能產生合理差異。
- 只有「純 indoor」或「純 outdoor」才調整，避免同時有兩個 tag 時變得不直覺。
- multiplier 的值在 config 裡：
  - `features.weather.rain_importance_multiplier.indoor`
  - `features.weather.rain_importance_multiplier.outdoor`

你可以把它理解成：「在室內，雨重要性降低；在室外，雨重要性提高」。

---

### 5.6 Step 5：混合雨與溫度（並做再正規化）

節錄：

```py
w_rain = w_rain_base * multiplier
w_temp = w_temp_base

denom = w_rain + w_temp
score = (w_rain * rain_score + w_temp * temp_score) / denom
score = clamp01(score)
```

拆解：

- 我們用「加權平均」混合兩個子分數。
- 因為 `w_rain` 可能被 multiplier 放大/縮小，所以最後一定要除以 `denom` 來 **再正規化**。
- `clamp01` 是保護：避免浮點誤差或 weird input 讓分數超出 0..1。

---

### 5.7 Step 6/7：Explainability（reasons + details）

節錄：

```py
reasons: list[str] = []
...
details = {
    "max_precipitation_probability": summary.max_precipitation_probability,
    "mean_temperature_c": summary.mean_temperature_c,
    "rain_score": rain_score,
    "temperature_score": temp_score,
    "rain_importance_multiplier": multiplier,
    "weights": {"rain": w_rain, "temperature": w_temp},
}
```

拆解：

- `reasons` 是給「列表 UI/CLI」用的短句，讓使用者知道為什麼這個分數是這樣。
- `details` 是給「debug/面板」用的結構化資料：
  - 你可以在 Web UI 做一個「展開」按鈕，把這些值畫出來（非常適合教學與產品調參）。

---

## 6) 可調參位置（你要去哪裡改）（中文）

天氣相關的 tuning knobs 在：

- `src/tripscore/config/defaults.yaml`
  - `ingestion.weather.comfort_temperature_c.min`
  - `ingestion.weather.comfort_temperature_c.max`
  - `ingestion.weather.temperature_penalty_scale_c`
  - `ingestion.weather.score_weights.rain`
  - `ingestion.weather.score_weights.temperature`
  - `features.weather.rain_importance_multiplier.indoor`
  - `features.weather.rain_importance_multiplier.outdoor`

---

## 7) 常見錯誤與排查（中文）

1) **`None` 當成 0 使用**
- `None` 代表資料缺失，程式會回 `neutral_score`，並在 reasons 裡寫 “unavailable”。
- 如果你看到很多景點 weather 都很接近，通常是 ingestion 抓不到或時間窗不在預報範圍。

2) **權重設錯導致回 neutral**
- 若 `w_rain + w_temp <= 0`，會直接回 `neutral_score` 並提示 “weights misconfigured”。

3) **舒適區間設得太窄**
- 例如 min=26 max=27 → 大部分時間都會被扣分，結果 weather 分數偏低。

4) **temperature_penalty_scale_c 設太小**
- scale 越小扣分越快；如果設成 1，超出 1°C 就會快速接近 0。

5) **tag 不一致**
- `destination.tags` 如果不是小寫 `indoor/outdoor`，multiplier 就不會生效。

---

## 8) 本階段驗收方式（中文 + 英文命令）

1) Run tests (offline):

```bash
PYTHONPATH=src pytest -q
```

預期：所有測試通過。

2) Syntax check:

```bash
python -m compileall -q src
```

預期：沒有輸出、exit code = 0。

3) CLI demo（看 weather reasons 與 details）：

```bash
PYTHONPATH=src python -m tripscore.cli recommend \
  --origin-lat 25.0478 --origin-lon 121.5170 \
  --start 2026-01-06T10:00+08:00 --end 2026-01-06T16:00+08:00
```

預期：每個目的地都會列出 `weather` component 的 reasons（雨機率與平均溫度）。

