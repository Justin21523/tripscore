# 01_phase1_bootstrap：啟動專案 + 設定系統（含 per-request `settings_overrides`）

本階段的目的，是把「怎麼把專案跑起來」以及「怎麼安全地在單次請求調參」講清楚。你不需要先懂所有演算法，只要能：

1. 在本機成功跑起 API/Web/測試  
2. 了解設定（config）從哪裡來、怎麼覆寫  
3. 知道 `settings_overrides` 的用途、限制、與常見錯誤  

---

## 1) 本階段目標（你完成後應該能做到什麼）

完成本階段後，你應該能：

- 用一組固定命令把 TripScore 跑起來（可重現）
- 明確知道「全域設定（defaults.yaml）」與「單次請求覆寫（settings_overrides）」的差別
- 看到錯誤訊息時，能靠 dotted path（例如 `features.context.district_factors_path`）定位是哪個 key 出問題

---

## 2) 本階段修改了哪些檔案？為什麼這樣拆？

### ✅ 改動/新增檔案

- `src/tripscore/config/overrides.py`
  - 這是「單次請求調參」的核心模組：**做 allowlist 驗證 + deep merge + Pydantic 再驗證**。
- `tests/test_settings_overrides.py`
  - 用 pytest 寫「安全性」與「正確性」測試，避免未來 refactor 時不小心打開危險的 override 能力。
- `docs/00_overview.md`
  - 給初學者的全局地圖：你知道自己在哪、要去哪。
- `docs/01_phase1_bootstrap.md`
  - 你正在讀的文件：詳細教你跑起來 + 了解設定覆寫。

### 為什麼要把 overrides 獨立成一個模組？

因為它是「安全邊界」：

- 前端（Web UI）會送出 JSON
- 後端必須保證：**只允許安全的 key/參數被覆寫**
- 如果把這段邏輯散落在 API route 或 recommender 裡，未來很容易「某次改動」就漏掉安全檢查

---

## 3) 核心概念講解（中文 + 中英對照）

### 3.1 Settings（設定）是什麼？

TripScore 的設定來源是 YAML（`src/tripscore/config/defaults.yaml`），讀進來後用 Pydantic 轉成 `Settings` 物件：

- **YAML（設定檔）**：人類好讀、易調參
- **Pydantic model（資料模型）**：程式內部用「有型別、有驗證」的方式使用設定

> 中英對照：Settings（設定）/ validation（驗證）/ schema（結構定義）

### 3.2 什麼是 per-request `settings_overrides`？

它是一個放在 API request body 裡的 JSON 物件，用來「只影響這一次推薦結果」：

- ✅ 好處：不用改 server 檔案、不用重啟服務，就能快速調參
- ✅ 好處：前端可以做成「控制面板」，像 Unity Inspector 一樣調數值
- ⚠️ 風險：如果允許覆寫太多 key，可能會讓使用者改到：
  - 秘密（secrets）
  - 網址（base_url）
  - 檔案路徑（file path）→ 甚至造成任意檔案讀取風險

所以我們必須做 **allowlist（白名單）**。

### 3.3 什麼是 allowlist tree？

我們用一個「樹狀結構」描述允許覆寫的 key：

- `True` 表示：這個 subtree 底下都可以覆寫
- `dict` 表示：只能覆寫 dict 裡列出來的 key（並且會遞迴檢查）

這種設計的優點：

- 錯誤訊息可以精準指出 `a.b.c` 哪裡不允許
- 以後要新增允許的參數，只需要改一個地方（集中管理）

---

## 4) 對應程式碼（分區塊貼上）

下面引用的程式碼來自：`src/tripscore/config/overrides.py`

### 4.1 Allowlist（白名單樹）

```python
ALLOWED_SETTINGS_OVERRIDES_TREE: dict[str, Any] = {
    "scoring": True,
    "features": {
        "weather": True,
        "parking": True,
        "preference_match": True,
        "context": {
            "default_avoid_crowds_importance": True,
            "default_family_friendly_importance": True,
            "crowd": True,
            "family": True,
        },
    },
    "ingestion": {
        "tdx": {"accessibility": True},
        "weather": {
            "aggregation": True,
            "comfort_temperature_c": True,
            "temperature_penalty_scale_c": True,
            "score_weights": True,
        },
    },
}
```

### 4.2 遞迴驗證：只保留允許的 keys

```python
def _filter_overrides(
    overrides: Mapping[str, Any],
    *,
    allowed_tree: Mapping[str, Any],
    path: tuple[str, ...] = (),
) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for key, value in overrides.items():
        if key not in allowed_tree:
            dotted_path = ".".join((*path, key))
            raise ValueError(f"settings_overrides contains a disallowed key: '{dotted_path}'")

        allowed = allowed_tree[key]
        if allowed is True:
            filtered[key] = value
            continue

        if not isinstance(value, Mapping):
            dotted_path = ".".join((*path, key))
            raise ValueError(f"settings_overrides key '{dotted_path}' must be a mapping")

        filtered[key] = _filter_overrides(value, allowed_tree=allowed, path=(*path, key))
    return filtered
```

### 4.3 Deep merge：把 override 疊到 Settings 上

```python
def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, override_value in override.items():
        if isinstance(override_value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), override_value)
            continue
        merged[key] = override_value
    return merged
```

### 4.4 主流程：validate → merge → Pydantic validate

```python
def apply_settings_overrides(settings: Settings, overrides: Mapping[str, Any] | None) -> Settings:
    if not overrides:
        return settings

    safe_overrides = _filter_overrides(overrides, allowed_tree=ALLOWED_SETTINGS_OVERRIDES_TREE)
    merged_payload = _deep_merge(settings.model_dump(mode="python"), safe_overrides)
    return Settings.model_validate(merged_payload)
```

---

## 5) 逐段解釋（這段做什麼、為什麼這樣做、還可以怎麼做）

### 5.1 Allowlist tree：為什麼要用「樹」？

因為 `settings_overrides` 是巢狀 JSON，你需要能表示「到某個層級可以自由改、到某個層級必須限制」。

例如：

- `scoring: True`：代表 `scoring` 底下的任何 key 都可以調（通常都是數字權重）
- `features.context`：我們**只允許數字/權重類**（`crowd`、`family`）  
  但刻意不允許 `district_factors_path`，避免讓使用者指定任意檔案路徑

> 這是初學者常忽略的點：**「能改參數」和「能改檔案路徑/網址」的安全風險完全不同。**

### 5.2 `_filter_overrides`：為什麼要「先驗證再合併」？

你可能會想：「我直接 merge 再用 Pydantic 驗證就好了吧？」  
不夠，原因是：

- Pydantic 主要驗證「型別/數值範圍」
- 我們需要驗證的是「這個 key 根本不該讓使用者改」  
  例如 `TDX_CLIENT_SECRET` 這種 **secret**，Pydantic 會覺得它只是字串，並不會阻止

所以 `_filter_overrides` 的責任是：

1. **拒絕不在 allowlist 的 key**（立即報錯，帶 dotted path）
2. **確保 restricted subtree 的 value 是 object**（避免 `{"ingestion": 1}` 這種形狀）
3. **只輸出安全子集**（再交給 merge）

### 5.3 `_deep_merge`：為什麼不能用 `dict.update()`？

`dict.update()` 是「淺層覆蓋」：

- 如果你做：
  - base: `{"ingestion": {"tdx": {"accessibility": {"radius_m": 500, "count_cap": 20}}}}`
  - override: `{"ingestion": {"tdx": {"accessibility": {"radius_m": 800}}}}`
- 用 update 的話會把整個 `accessibility` dict 覆蓋掉，`count_cap` 會消失

deep merge 的價值是：只改你指定的那幾個 key，其他保持原樣。

### 5.4 `apply_settings_overrides`：為什麼最後還要 `Settings.model_validate()`？

即使 key 都合法，你還是可能送出不合法的值，例如：

- `radius_m = -1`
- `parking_risk_weight = 2.0`

這些「數值範圍」就交給 Pydantic 的 schema 來擋，這樣才是完整的防線：

1) allowlist 擋「不該改的 key」  
2) Pydantic 擋「不合法的值」  

---

## 6) 常見錯誤與排查（新手最常卡這裡）

### 6.1 `settings_overrides contains a disallowed key: 'x.y.z'`

表示你送的 JSON 裡有某個 key 不在 allowlist。

排查步驟：

1. 看錯誤訊息中的 dotted path（例如 `features.context.district_factors_path`）
2. 回到 `ALLOWED_SETTINGS_OVERRIDES_TREE` 看是不是被刻意禁止
3. 如果你真的需要這個能力：不要先硬開放，先討論安全性（例如路徑是否只允許 `data/` 底下）

### 6.2 `settings_overrides key 'ingestion' must be a mapping`

表示你在某個 subtree 的 value 送錯型別（應該是 object/dict，你卻送了 number/string）。

例如錯誤：

```json
{ "ingestion": 1 }
```

應該改成：

```json
{ "ingestion": { "tdx": { "accessibility": { "radius_m": 800 } } } }
```

### 6.3 「我改了 overrides，但看起來沒生效」

常見原因：

1. 你把 override 放到錯的 key
2. 你送到的是「custom preset name」，但 API 的 `preset` 欄位只認 server 內建 presets  
3. 你的 UI 沒有勾選 “Enable tuning overrides”（如果你用的是 Web UI）

---

## 7) 本階段驗收方式（中文說明 + 英文命令）

### 7.1 跑測試（離線、最快）

預期：pytest 全部通過。

```bash
PYTHONPATH=src pytest -q
```

### 7.2 跑 API/Web（手動驗收）

預期：服務啟動無錯誤，打開首頁後可送出推薦請求。

```bash
PYTHONPATH=src uvicorn tripscore.api.app:app --reload --port 8000
```

### 7.3 用 curl 驗證 `settings_overrides`（可選）

下面範例把 bus radius 改大（只影響本次請求）：

```bash
curl -s http://127.0.0.1:8000/api/recommendations \\
  -H 'Content-Type: application/json' \\
  -d '{
    "origin": {"lat": 25.0478, "lon": 121.5170},
    "time_window": {"start": "2026-01-05T10:00+08:00", "end": "2026-01-05T18:00+08:00"},
    "max_results": 3,
    "settings_overrides": {
      "ingestion": {"tdx": {"accessibility": {"radius_m": 900}}}
    }
  }' | python -m json.tool
```

預期：回傳 JSON，且在 `query.settings_overrides` 會看到你送出的 override。

