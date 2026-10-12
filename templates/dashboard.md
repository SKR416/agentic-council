# 視覺化儀表板規範

只在使用者接受步驟 4 的圖解選項後才讀取本檔。

生成的 HTML 必須寫進**本次執行資料夾**：`<執行資料夾>/plan_preview.html`。
不得寫到 `.tmp/plan_preview.html` 之類的固定路徑，否則下一次執行會覆蓋上一次的結果。

頁面為單一 HTML 檔，但**需要網路連線**才能完整顯示：下列依賴以 CDN 載入，離線開啟時
排版與圖表都不會出現。產生檔案後必須在終端一併告知使用者這一點。

- 排版：Tailwind CSS CDN
- 拓撲與流程圖：Mermaid.js CDN
- 多維評分矩陣：Chart.js CDN（雷達圖呈現各席位的六維評分）
  - 數據來源只能是 `audit.json` 中的評分，主 Agent 不得調整、平滑或補值。
  - 缺席席位不繪製於雷達圖中，並在圖例旁以文字標註「X 席缺席」。
  - 若為降級模式產出，頁面頂部必須顯示降級模式警示。

本機啟動指令（`<執行資料夾>` 代入本次的 `.council/runs/<時間戳記>`）：

- Windows: `cmd /c start <執行資料夾>\plan_preview.html`
- macOS: `open <執行資料夾>/plan_preview.html`
- Linux: `xdg-open <執行資料夾>/plan_preview.html`
