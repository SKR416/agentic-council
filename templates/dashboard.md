# 視覺化儀表板規範

只在使用者接受步驟 4 的圖解選項後才讀取本檔。

## 產生方式

儀表板一律由固定範本 `templates/dashboard.html` 產生。主 Agent 只填資料，不設計頁面：

1. 把 `templates/dashboard.html` 複製到**本次執行資料夾**：`<執行資料夾>/plan_preview.html`。
   不得寫到 `.tmp/plan_preview.html` 之類的固定路徑，否則下一次執行會覆蓋上一次的結果。
2. 只替換 `<!-- COUNCIL-DATA:BEGIN -->` 與 `<!-- COUNCIL-DATA:END -->` 之間的 JSON。
   範本的 CSS、JS 與其餘 HTML 一律不得修改、增刪或「順手改善」。
3. 寫入後以 `python -c` 或同等方式抽出該段 JSON 做一次解析，確認格式正確。

原因：範本固定，每次產出的版面、配色與圖表畫法才會一致；評分的呈現方式也由範本決定，主 Agent 無法透過版面設計放大或淡化某一席的意見。

## 資料欄位

JSON 結構與範例以範本內建的範例資料為準，填寫規則如下：

- `meta.is_sample` 設為 `false`。`dispatch_mode`、`audit_method`、`retry_count` 與執行狀態列一致。
- `audit`：`audit.json` 的 `seats` 與 `aggregate` **原封不動貼入**，不得刪減、改寫或重新計算。
  頁面上的評分、立場分布、評分撕裂全部由範本從這裡讀取。
- `synthesis`：步驟 3、4 已寫好的內容，逐字搬入對應欄位，不得在此新增結論：
  - `absent_reasons`：缺席席位與原因。
  - `conflicts`、`high_risk_blind_spots`：步驟 3 的深層矛盾與高危盲點。
  - `tendency`：傾向對照的結果（一致 / 部分一致 / 相反）與說明。
  - `coverage`：覆蓋率聲明，逐條。
  - `baseline_dispositions`：防禦性底線處置表，每一條都要出現。
  - `plan`：硬化後的主方案，`sources` 填席位編號；主代理新增的條款填空陣列。
  - `circuit_breakers`、`next_actions`：熔斷條件與即刻行動清單。
- JSON 字串中的 `</` 必須寫成 `<\/`，否則會提前結束 script 區塊。

## 呈現規則（由範本執行，主 Agent 不需處理）

- 缺席席位不繪製雷達圖、評分顯示「—」，並標註「X 席缺席」。
- 降級模式、人工稽核時，頁面頂部顯示警示。
- 所有文字以純文字插入，報告內容中的 HTML 不會被執行。

## 開啟方式

頁面不需網路即可完整顯示；只有字型從 Google Fonts 載入，離線時改用系統字型。

本機啟動指令（`<執行資料夾>` 代入本次的 `.council/runs/<時間戳記>`）：

- Windows: `cmd /c start <執行資料夾>\plan_preview.html`
- macOS: `open <執行資料夾>/plan_preview.html`
- Linux: `xdg-open <執行資料夾>/plan_preview.html`
