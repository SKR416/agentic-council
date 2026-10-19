# 執行資料夾檔案格式（v2.5）

執行資料夾：`.council/runs/<YYYYMMDD-HHMMSS>/`。本文件是主 Agent、`scripts/audit_council.py`、`scripts/build_dashboard.py` 三方共用的契約；改格式時三方要一起改。

| 檔案 | 寫入者 | 讀取者 |
|---|---|---|
| `brief.md` | 主 Agent（步驟 1） | 稽核腳本 |
| `seats.json` | 主 Agent（步驟 2、2.5） | 稽核腳本 |
| `audit.json` | 稽核腳本 | 主 Agent、儀表板腳本 |
| `summary.md` | 稽核腳本 | 主 Agent（Mode B 對話輸出） |
| `report_full.md` | 稽核腳本 | 使用者（完整報告） |
| `plan.md` | 主 Agent（步驟 4） | 稽核腳本 `--check-plan` |
| `dashboard_input.json` | 主 Agent | 儀表板腳本 |
| `plan_preview.html` | 儀表板腳本 | 使用者 |

## seats.json

```json
{
  "parent_id": "<主 Agent 自身的對話 ID>",
  "seats": [
    {"seat": 1, "conversation_id": "<子代理 ID>", "transcript": "<transcript_full.jsonl 路徑>"},
    {"seat": 3, "conversation_id": "<重派的子代理 ID>", "transcript": "...",
     "retry": {"count": 1, "hint_ids": ["R2"], "previous_transcript": "<前一次的 transcript 路徑>"}},
    {"seat": 5, "conversation_id": "...", "transcript": "...",
     "role_check": {"pass": false, "reason": "<一句話>", "quote": "<角色卡原文>"}}
  ]
}
```

### transcript 路徑

- 必須是 `<brain>/<conversation_id>/.system_generated/logs/transcript_full.jsonl`，其中 `<conversation_id>` 與同一筆的 `conversation_id` 相同。
- `<brain>` 是 agy 的 brain 資料夾：預設 `~/.gemini/antigravity/brain` 與 `~/.gemini/antigravity-cli/brain`；稽核腳本以 `--brain-dir`（可重複）覆寫，測試一律傳入暫存資料夾。
- 路徑不符：該席判「未稽核」，不讀取該檔（D3）。

### retry（重派）

- 物件格式：`{"count": <int ≥1>, "hint_ids": [<retry_menu.md 的編號>...], "previous_transcript": <str|null>}`。
- `hint_ids` 最多 `RETRY_HINT_MAX`（2）個，每個都必須存在於 `templates/retry_menu.md`。
- 相容：舊版布林 `"retry": true` 視為 `{"count": 1, "hint_ids": [], "previous_transcript": null}`。
- 重派任務 = 簡報 + 角色卡 + 輸出規格（逐字）+ 一個空行 + `hint_ids` 依序對應的條目全文，每條獨立一行。除此之外不得有任何文字。

### role_check（離題排除，D4）

- 只有一種寫法：`{"pass": false, "reason": <str>, "quote": <str>}`。`pass` 為 true 或欄位不存在都等於「沒有排除」。
- `quote` 必須是該席角色卡「必答問題：」或「禁止：」段落中**完整的一條**（逐字，可省略開頭的 `- `；比對前做 `norm()`）。片段不算。不符：忽略此排除，並在 `audit.json` 頂層 `config_errors` 報錯。
- 只能排除，不能救回：腳本判定未通過的席位，不因 `role_check` 改變判定。

## templates/retry_menu.md 與 templates/preset_menu.md

條目格式（腳本以此 regex 解析，其他行一律忽略）：

```
^- (R\d+)\s+(.+)$      # retry_menu.md：編號、條目全文
^- (P\d+)\s+(.+)$      # preset_menu.md
```

條目全文必須是單行完整句子，不得含換行。

## 報告欄位（schemas/subagent_output.md）

欄位順序固定如下（順序錯只記警告，D8）：

```
【席位】【立場裁定】【信心程度】【六維評分】【必答回應】【最強支持理由】【致命盲點】【防禦性底線】
```

`【必答回應】`（Phase 2 新增）：每一題一行，格式 `Q<n>：<回應>`，`<n>` 對應角色卡「必答問題」的順序（1 起算）。腳本以 `^\s*Q(\d+)\s*[:：]\s*\S` 計數不重複的題號，少於 `MUST_ANSWER_MIN`（2）判「格式不合格」（D6）。

## audit.json

```json
{
  "seats": [
    {
      "seat": 1,
      "conversation_id": "...",
      "verdict": "通過 | 派發錯誤 | 隔離違規 | 格式不合格 | 未稽核 | 缺席",
      "excluded_by_role_check": false,
      "role_check_reason": null,
      "retried": false,
      "must_answer_count": 3,
      "warnings": ["..."],
      "isolation": [], "dispatch": [], "residual": null,
      "format_errors": [], "format_warnings": [],
      "tools_used": [], "stance": "...", "scores": {}, "chars": 0, "report": "..."
    }
  ],
  "aggregate": {"valid_seats": [], "stance_distribution": {}, "tears": []},
  "config_errors": [],
  "anchoring_terms": [{"term": "...", "seats": [1, 4]}],
  "unsourced_numbers": [{"value": "...", "context": "..."}]
}
```

- `verdict`：`缺席` 只用於 `seats.json` 沒有登錄的席位。
- `excluded_by_role_check`：`role_check` 生效時為 true；該席 `verdict` 不變，但不算票。
- `role_check_reason`：`role_check` 生效時為 seats.json 的 `reason` 字串，否則 null（儀表板顯示用）。
- `retried`：`seats.json` 有 `retry`（物件或 true）即為 true。
- `warnings`：與 `format_warnings` 相同內容的別名，供儀表板使用；新程式讀 `warnings`。
- `must_answer_count`：Phase 1 先填 `null`，A13 完成後填實數。
- 只有 `verdict == "通過"` 且 `excluded_by_role_check == false` 的席位進入 `aggregate`（A8）。
- `anchoring_terms`（A12）：2 位以上（`ANCHOR_MIN_SEATS`）通過席位使用、且已出現在 `brief.md` 的詞。Phase 1 先輸出空陣列。
- `unsourced_numbers`（A10）：`plan.md` 中出現、但在 `brief.md` 與通過席位（且未被 role_check 排除）報告中都找不到的數字，每筆附 `context`（前後文）。Phase 1 先輸出空陣列。

## plan.md 與 --check-plan（A10）

- 步驟 4 寫完「硬化後的主方案」後，主 Agent 把方案全文（含標題、表格）存成 `plan.md`，再執行：
  `python audit_council.py --skill-dir <skill> --run-dir <run> --check-plan`
- `--check-plan` 讀取既有的 `audit.json`（不重新稽核席位），比對 `plan.md` 的數字，把結果寫回 `audit.json` 的 `unsourced_numbers` 並輸出 Markdown 清單。
- 有任何 `unsourced_numbers` 時結束碼非 0。主 Agent 必須把那些數字改寫成待確認的驗證步驟後重跑，直到清單為空（D1，不開例外）。

## summary.md 與 report_full.md（A15）

- `summary.md`：Mode B 對話中貼出的摘要。每席一段，直接擷取報告原句（立場、最強支持理由、致命盲點的「場景」），不改寫；末尾附稽核表。
- `report_full.md`：六席完整報告原文與完整稽核表。
