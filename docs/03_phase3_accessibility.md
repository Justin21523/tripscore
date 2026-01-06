# Phase 3 — Accessibility Feature（可達性特徵）教學

## 1) 本階段目標（你要學到什麼）

這個 Phase 的目標是讓你能「看懂 + 自己改得動」TripScore 的 **可達性（Accessibility）** 計分模組。你會學到：

- 可達性在 TripScore 的資料流中扮演什麼角色（ingestion → features → scoring）。
- 我們如何把「原始交通資料」轉成「可解釋、可調參」的分數。
- 什麼是 **metrics（原始指標）**、什麼是 **score（0..1 分數）**，以及為什麼要分開。
- 哪些參數可以在 `src/tripscore/config/defaults.yaml` 調整（半徑、cap、權重、混合比例）。
- 常見的坑：資料缺漏（None vs 0）、權重配置錯誤、效能（O(N) 掃描）、距離單位（m vs km）。

> 本專案第一版堅持「規則/加權評分」而不是 ML：因為我們要 **可解釋**、能快速迭代、能用 config 調參，而不是先被模型複雜度綁死。

---

## 2) 這個 Phase 改了哪些檔案？為什麼這樣拆？

本階段只專注在一個模組（符合「一次一個 Phase / 一個模組」的規則）：

- `src/tripscore/features/accessibility.py`
  - 加入逐行英文註解（初學者可讀、說明 why + what）。
  - 保留既有功能與輸出 shape（不改 API contract）。
  - 把「可達性」的設計意圖、資料缺漏處理（fail-open）、與可調參位置講清楚。

另外新增一份對應教學文件：

- `docs/03_phase3_accessibility.md`
  - 用超詳細中文把上述模組拆段講解，並引用程式碼區塊。

---

## 3) 核心概念講解（中英對照）

以下是你讀懂本模組必須先熟悉的名詞：

- **Accessibility（可達性）**：某個目的地在你可用時間窗內「去那裡方不方便」的 proxy 分數。
- **Origin proximity（出發點接近度）**：目的地離出發點越近越好（距離越短分數越高）。
- **Local transit（在地大眾運輸）**：目的地附近的交通資源（公車站、捷運站、共享單車站）。
- **Metric（原始指標）**：像「500m 內公車站數量」、「最近捷運站距離」這種原始數字。
- **Score（分數）**：把 metric 轉成 0..1 的標準化值，方便跟其他模組一起加權。
- **Normalization（正規化）**：把不同尺度的值（數量、距離、溫度）轉成可比較的 0..1。
- **Cap（上限）**：例如 `count_cap=20` 表示「超過 20 個就當作滿分」避免極端值主宰。
- **Fail-open（失敗開放）**：外部資料拿不到時，不讓系統崩潰，而是回傳中性的 `neutral_score`。
- **Sentinel（哨兵值）**：例如 `float("inf")` 表示「沒有找到最近站點」的特殊值。
- **Explainability（可解釋性）**：回傳 `reasons`（文字原因）與 `details`（結構化細節）讓 UI 能拆解分數。

---

## 4) 可達性分數的資料流（你要先建立的 mental model）

在 `src/tripscore/recommender/recommend.py` 的主流程裡，對每個 `Destination` 會做：

1. ingestion：抓 bus/metro/bike 的站點資料（TDX）
2. features：計算 `AccessibilityMetrics`
3. features：把 metrics 轉成 `accessibility score`
4. scoring：與 weather/preference/context 一起加權成 composite score
5. explain：把每個 component 的 score/weight/reasons/details 組成 breakdown

你現在看的 `features/accessibility.py`，就是「第 2 與第 3 步」。

---

## 5) 程式碼分區塊引用 + 逐段講解

### 5.1 `AccessibilityMetrics`：為什麼要先做「原始指標」？

`src/tripscore/features/accessibility.py`（節錄）：

```py
@dataclass(frozen=True)
class AccessibilityMetrics:
    bus_stops_within_radius: int | None
    bus_nearest_stop_distance_m: float | None
    bike_stations_within_radius: int | None
    bike_nearest_station_distance_m: float | None
    bike_available_rent_bikes_within_radius: int | None
    bike_available_return_bikes_within_radius: int | None
    metro_stations_within_radius: int | None
    metro_nearest_station_distance_m: float | None
    origin_distance_m: float
```

重點理解：

- 我們把「交通資料」先轉成 metrics，是為了讓後面的 scoring 更乾淨、可替換。
- 這些欄位很多是 `| None`：`None` 的語意是「資料拿不到」，不是 0。
  - 例如：`bus_stops_within_radius=None` 代表 TDX bus dataset 沒拿到（或被關掉）。
  - `bus_stops_within_radius=0` 才代表「資料存在但附近真的沒有站」。
- `origin_distance_m` 不是 optional：因為只要你有 origin + destination 座標，就能算距離。

---

### 5.2 `compute_accessibility_metrics(...)`：從站點清單算出 metrics

`src/tripscore/features/accessibility.py`（節錄）：

```py
dest_pt = CoreGeoPoint(lat=destination.location.lat, lon=destination.location.lon)
origin_pt = CoreGeoPoint(lat=origin.lat, lon=origin.lon)
origin_distance_m = haversine_m(origin_pt, dest_pt)
```

你要知道的事情：

- `haversine_m` 是計算球面兩點距離（大圓距離）的常見公式，對城市尺度 ranking 夠用。
- 我們統一把座標轉成 `CoreGeoPoint`：這是一種「避免各模組自行算距離而出錯」的工程手法。

接著是 bus/metro/bike 三段都類似：掃描清單，算「附近 count」與「最近距離」。

以 bus 為例（節錄）：

```py
if not bus_stops:
    bus_within = None
    bus_nearest_m = None
else:
    bus_nearest_m: float | None = None
    bus_within = 0
    for stop in bus_stops:
        stop_pt = CoreGeoPoint(lat=stop.lat, lon=stop.lon)
        distance_m = haversine_m(dest_pt, stop_pt)
        bus_nearest_m = distance_m if bus_nearest_m is None else min(bus_nearest_m, distance_m)
        if distance_m <= bus_radius_m:
            bus_within += 1
    bus_nearest_m = bus_nearest_m if bus_nearest_m is not None else float(\"inf\")
```

逐段理解：

1) `if not bus_stops`：這不是「附近沒站」，而是「整份站點清單不存在」  
→ 所以 metrics 用 `None`，讓 scoring 走 fail-open（回 neutral）。

2) `for stop in bus_stops`：這是 MVP 最簡單的做法，缺點是效能  
→ 你有 `D` 個景點、`N` 個站，就會是 O(D*N)。景點變多時必須做空間索引（後面會提）。

3) `float("inf")`：是哨兵值  
→ 代表「理論上應該有值，但因為資料異常仍沒有算到」，避免 `None` 跟缺資料混在一起。

---

### 5.3 `score_accessibility(...)`：把 metrics 轉成 0..1 分數（可解釋）

這個函式做三件事：

1. origin proximity（出發點距離）
2. local transit（bus + metro + bike）
3. blend（把 1 與 2 混合成最終 accessibility score）

#### (1) Origin proximity

節錄：

```py
origin_cap_m = int(cfg.origin_distance_cap_m)
origin_score = 1 - clamp01(metrics.origin_distance_m / origin_cap_m)
```

為什麼要 cap？

- 不 cap 的話，距離尺度會被極端值拉爆（例如 1km vs 50km）。
- cap 的設計是：「超過 cap 就當作 0 分」，在 ranking 上更直觀。

#### (2) Local transit：子分數（bus / metro / bike）

bus 的核心概念是把：

- count（附近站點數量）
- distance（最近站距離）

各自轉成 0..1，然後依權重組合。

節錄（bus count/distance → weighted average）：

```py
count_score = min(metrics.bus_stops_within_radius, cfg.count_cap) / max(cfg.count_cap, 1)
distance_score = 1 - min(metrics.bus_nearest_stop_distance_m, cfg.distance_cap_m) / max(cfg.distance_cap_m, 1)
bus_score = clamp01((w_count * count_score + w_distance * distance_score) / denom_local)
```

你要抓到的「產品視角」：

- `count_cap` 越小：更容易滿分 → 代表你更在意「有沒有基本覆蓋」而不是「超密集」。
- `distance_cap_m` 越小：只要一離開就扣很多分 → 代表你更在意「最近站要很近」。
- `w_count / w_distance`：代表你更在意密度還是最近距離。

bike 的特別點在於「可用車輛」：

- station density 代表「選擇多不多」
- available bikes 代表「最後一哩路真的能不能用」

#### (3) Local transit 的三訊號混合（bus + metro + bike）

節錄：

```py
signal_weights = normalize_weights(raw_signal_weights)
local_transit_score = clamp01(
    signal_weights[\"bus\"] * bus_score
    + signal_weights[\"metro\"] * metro_score
    + signal_weights[\"bike\"] * bike_score
)
```

重點：

- `normalize_weights` 讓權重加總變 1，避免你改 config 後總和不是 1 導致不可預期。
- 如果某個 dataset 缺了（available=False），我們把它權重設成 0，避免「缺資料反而影響結果」。

---

## 6) 可調參位置（你要去哪裡改權重/門檻）

所有可達性參數都在：

- `src/tripscore/config/defaults.yaml`
  - `ingestion.tdx.accessibility.*`

你最常會調的是：

- `radius_m`：目的地附近「走路可接受」半徑
- `count_cap`：附近站點數量滿分上限
- `distance_cap_m`：最近站距離扣分尺度
- `local_score_weights.count / distance`：bus 的 count vs distance 比重
- `metro.*` / `bike.*`：各自的半徑、cap 與權重
- `local_transit_signal_weights.{bus,metro,bike}`：三訊號混合比例
- `blend_weights.local_transit / origin_proximity`：最後把 local transit 與出發點距離混合的比例

---

## 7) 常見錯誤與排查（很重要）

1) **None vs 0 搞混**
- `None`：資料不存在（TDX 失敗、被關掉、抓不到）
- `0`：資料存在但附近真的沒有

2) **權重設錯導致一直回 neutral**
- 例如 `blend_weights.local_transit=0` 且 `origin_proximity=0`
- 模組會 fail-open 回 `neutral_score` 並給出原因字串（可在 UI/CLI 看到）

3) **半徑/門檻設太嚴格**
- `radius_m` 太小 → 附近 count 常常是 0，local transit 分數偏低
- `distance_cap_m` 太小 → 只要稍遠就扣到底

4) **效能問題（景點變多會卡）**
- 目前是每個景點掃一遍站點清單（O(D*N)）
- 真正要上線，需要空間索引（例如 geohash grid / k-d tree）或預先把站點分桶

5) **座標順序錯（lat/lon 顛倒）**
- 會造成距離暴增、分數異常
- 你可以用 CLI 對單一點做 debug：看 `details` 裡的 `origin_distance_m`

---

## 8) 本階段驗收方式（中文 + 英文命令）

你可以用以下方式確認本 Phase 可驗收（預期 console 無錯誤）：

1) Run tests (offline):

```bash
PYTHONPATH=src pytest -q
```

預期：所有測試通過（例如 `10 passed`）。

2) Basic syntax check:

```bash
python -m compileall -q src
```

預期：沒有任何輸出、exit code = 0。

3) CLI quick demo（看可達性 reasons）：

```bash
PYTHONPATH=src python -m tripscore.cli recommend \
  --origin-lat 25.0478 --origin-lon 121.5170 \
  --start 2026-01-06T10:00+08:00 --end 2026-01-06T16:00+08:00
```

預期：你會看到 Top results，每個景點的 `accessibility` 會印出多條 reasons（距離、附近站點等）。

