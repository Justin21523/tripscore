# Phase 5 — Preference Match Feature（偏好匹配/標籤加權）教學

## 1) 本階段目標（中文）

這個 Phase 的目標是讓你能完全理解 TripScore 的 **偏好匹配（Preference Match）** 是怎麼運作的：使用者用「tag 權重」描述喜好（例如 indoor/outdoor/food/culture），系統再把每個目的地的 tags 轉成一個 **0..1 的可解釋分數**。

你會學到：

- 為什麼我們用「tag + weight」做 MVP：可解釋、可調參、很好做 UI 面板。
- Preference Match 的分數公式：`matched_weight_sum / positive_weight_sum`。
- `None`、空 dict、負權重在本 MVP 的語意差異（這是最常踩坑的地方）。
- `required_tags` / `excluded_tags` 為什麼不在這裡處理，而是在 recommender 先做候選 pruning。

---

## 2) 本階段改了哪些檔案？為什麼（中文）

本 Phase 只處理一個模組（符合「一次一個 Phase / 一個模組」）：

- `src/tripscore/features/preference_match.py`
  - 加上逐行（或小區塊）英文註解，解釋 why + what，並說明設計取捨（例如負權重的處理）。
  - 保留原本輸出 shape（`score`, `details`, `reasons`）避免破壞 API / UI。

新增教學文件：

- `docs/05_phase5_preference_match.md`（本文件）

---

## 3) 核心概念講解（中英對照）

- **Tag（標籤）**：目的地的類型/屬性字串，例如 `indoor`, `food`, `culture`。
- **Tag weight（標籤權重）**：使用者或 preset 對 tag 的偏好強度（越大代表越想要）。
- **Positive weight（正權重）**：在 MVP 中我們只把 `> 0` 的權重視為「加分項」。
- **Preference score（偏好分數）**：把「有命中哪些 tags」轉成 0..1 的分數。
- **Normalization（正規化）**：除以「所有正權重總和」讓分數落在 0..1，方便跟其他 feature 一起加權。
- **Candidate filtering（候選過濾）**：`required_tags` / `excluded_tags` 會在推薦流程早期過濾候選，避免之後做無用計算。

---

## 4) Preference Match 在整體資料流的位置（中文）

在 `src/tripscore/recommender/recommend.py` 中，大致是：

1) 先把 `tag_weights` 的來源整合成「有效值」（config defaults → preset → user override）  
2) 再把目的地列表做 `required_tags/excluded_tags` 過濾（快速 prune）  
3) 對每個候選目的地呼叫：

- `features/preference_match.py::score_preference_match(destination, preferences, settings)`

因此 **這個模組專注做「偏好分數」**，而不是「候選過濾」。

---

## 5) 程式碼分區塊引用 + 逐段解釋（中文）

### 5.1 入口：`score_preference_match(...)`

檔案：`src/tripscore/features/preference_match.py`（節錄）

```py
def score_preference_match(
    destination: Destination, *, preferences: UserPreferences, settings: Settings
) -> tuple[float, dict, list[str]]:
```

你要知道：

- `destination.tags` 來自 catalog（例如 `data/catalogs/destinations.json`）。
- `preferences.tag_weights` 可能來自：
  - 使用者（CLI/Web 表單）
  - preset（例如 rainy_day_indoor）
  - config default（`features.preference_match.tag_weights_default`）

---

### 5.2 權重來源選擇（最容易踩坑：空 dict 的語意）

節錄：

```py
tag_weights = (preferences.tag_weights or settings.features.preference_match.tag_weights_default) or {}
```

這行的語意非常重要：

- 如果 `preferences.tag_weights` 是 `None` → 會用 config default  
- 如果 `preferences.tag_weights` 是 `{}`（空 dict）→ 因為 Python 把空 dict 當作 False，**也會回退到 config default**  

也就是說：**目前 MVP 不支援用空 dict 來「關掉偏好匹配」**。  
如果未來你真的要支援「關掉」，可以用明確 flag（例如 `enable_preference=False`）或新增設定值來控制，但那會是新的需求（會改行為），現在我們先不做。

---

### 5.3 為什麼只吃正權重（> 0）？

節錄：

```py
positive_weights = {k: float(v) for k, v in tag_weights.items() if float(v) > 0}
max_score = sum(positive_weights.values())
```

這是 MVP 的設計取捨：

- 我們把「喜歡」當成加分項（正權重）。
- 「不喜歡/避免」在 MVP 用 `excluded_tags` 做 hard filter，避免在這裡引入負分的複雜度。
- 這樣做的好處：
  - 分數更直覺（0..1）
  - explain 更好寫
  - UI 面板更容易調參

`max_score` 是正規化的分母：如果一個目的地命中所有正權重 tag，就會拿到 1.0。

---

### 5.4 如何計算命中分數：`matched_weight_sum / positive_weight_sum`

節錄：

```py
matched = [t for t in destination.tags if t in positive_weights]
matched_score = sum(positive_weights[t] for t in matched)

if max_score <= 0:
    score = float(settings.scoring.neutral_score)
else:
    score = matched_score / max_score
score = clamp01(score)
```

解釋：

- `matched`：目的地 tags 與正權重 tags 的交集（intersection）。
- `matched_score`：把命中的每個 tag 權重加總。
- `score`：
  - 正常情況：`matched_score / max_score`（自然落在 0..1）
  - 如果 `max_score <= 0`：代表沒有正權重（或全是 0/負），我們 fail-open 回 `neutral_score`。
- `clamp01` 是最後保護：避免浮點誤差或 weird input。

---

### 5.5 Explainability：reasons + details

節錄：

```py
if matched:
    reasons.append("Matches: " + ", ".join(matched[:6]))
else:
    reasons.append("No strong tag match")

details = {
    "matched_tags": matched,
    "tag_weights_used": positive_weights,
}
```

你要知道：

- `reasons`：給 CLI/UI 列表看的一行解釋（控制長度，避免太吵）。
- `details`：給「面板/Debug」看，用來做分數拆解（非常適合做 UI 的 expandable panel）。

---

## 6) 可調參位置（中文）

預設 tag 權重在：

- `src/tripscore/config/defaults.yaml`
  - `features.preference_match.tag_weights_default.*`

你可以用它做：

- 產品預設偏好（例如更偏向 indoor）
- 讓沒有填偏好的使用者也能有合理排序（baseline behavior）

---

## 7) 常見錯誤與排查（中文）

1) **空 dict `{}` 沒有關掉偏好匹配**
- 因為 `(preferences.tag_weights or defaults)` 的設計會回退到 defaults。

2) **負權重沒效果**
- 這個 MVP 只使用 `> 0` 權重做加分。
- 想「避免某些 tag」請用 `excluded_tags`（或 preset 的 excluded_tags）。

3) **tag 大小寫不一致**
- 我們的 catalog tags 預期是小寫（`indoor`, `food`）。
- 如果你在 catalog 放了 `Indoor`，就不會 match 到 `indoor`。

4) **所有分數都接近 0.5**
- 通常是 `positive_weights` 為空 → `max_score <= 0` → 直接回 `neutral_score`。
- 請檢查 config 的 `features.preference_match.tag_weights_default` 是否存在且為正數。

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

3) CLI demo（看 preference reasons）：

```bash
PYTHONPATH=src python -m tripscore.cli recommend \
  --origin-lat 25.0478 --origin-lon 121.5170 \
  --start 2026-01-06T10:00+08:00 --end 2026-01-06T16:00+08:00 \
  --indoor 1.0 --outdoor 0.0 --culture 0.7 --food 0.6
```

預期：你會看到每個目的地的 `preference` component reasons 顯示 "Matches: ..." 或 "No strong tag match"。

