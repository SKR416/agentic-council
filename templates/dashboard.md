# 視覺化儀表板規範

步驟 4 完成後預設直接產生儀表板，產生前讀取本檔，不需詢問使用者。

## 產生方式

儀表板一律由固定範本 `templates/dashboard.html` 經腳本產生。主 Agent 只寫資料，不設計頁面，也不手動編輯 HTML：

1. 把要呈現的內容寫進 `<執行資料夾>/dashboard_input.json`（格式見下方「資料欄位」）。
2. 執行：
   ```
   python <skill 資料夾>/scripts/build_dashboard.py --skill-dir <skill 資料夾> --run-dir <執行資料夾>
   ```
   腳本會讀取同一資料夾的 `audit.json`，產生 `<執行資料夾>/plan_preview.html`。
   不得改寫到 `.tmp/plan_preview.html` 之類的固定路徑，否則下一次執行會覆蓋上一次的結果。
3. 腳本報錯時依訊息修正 `dashboard_input.json` 後重跑，不得改用手動複製範本、自行填入的方式。

原因：範本固定，每次產出的版面、配色與圖表畫法才會一致；評分的呈現方式也由範本決定，主 Agent 無法透過版面設計放大或淡化某一席的意見。評分資料由腳本直接從 `audit.json` 帶入，主 Agent 沒有轉抄的機會。報告內容可能含有來自網路的文字，腳本會跳脫其中的 `<`、`>`、`&`，手動填入無法保證這一點。

無法執行 Python 時（步驟 0.5 的檢查未通過），不產生儀表板，並告知使用者原因。

## 資料欄位

`dashboard_input.json` 只有 `meta` 與 `synthesis` 兩個物件，欄位以範本內建的範例資料為準：

- `meta`：`title`、`run_id`、`generated_at`、`dispatch_mode`（標準 / 降級）、`audit_method`（腳本 / 人工）、`retry_count`，與執行狀態列一致。`is_sample` 由腳本設定，不必填。
- 評分、立場分布、評分撕裂不要寫進這個檔案，腳本會從 `audit.json` 帶入。
- `synthesis`：步驟 3、4 已寫好的內容，逐字搬入對應欄位，不得在此新增結論：
  - `absent_reasons`：缺席席位與原因。
  - `conclusion`（選填）：步驟 4 仲裁結論的一句話版本，從已寫好的結論逐字摘出，不得新增判斷。頁面會把它放在最上方的「結論摘要」，並自動附上傾向對照與前三條熔斷條件；沒填則改用 `tendency.text`。
  - `conflicts`、`high_risk_blind_spots`：步驟 3 的深層矛盾與高危盲點。
  - `tendency`：傾向對照的結果（一致 / 部分一致 / 相反）與說明。
  - `coverage`：覆蓋率聲明，逐條。
  - `baseline_dispositions`：防禦性底線處置表，每一條都要出現。
  - `plan`：硬化後的主方案，`sources` 填席位編號；主代理新增的條款填空陣列。每條可加選填的 `time`（時間或階段，例如「11:45」「16:40-17:25」「拆分前」）。只要有任一條填了 `time`，頁面就改用時間軸版面，時間顯示在左欄；行程類議題建議每條都填，沒有時間概念的議題可以全部不填。
  - `circuit_breakers`、`next_actions`：熔斷條件與即刻行動清單。

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
