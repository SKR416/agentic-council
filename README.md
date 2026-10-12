# agentic-council

給 [Antigravity](https://antigravity.google/)（agy）使用的決策智囊團 skill，採用 Orchestrator-Subagent 架構。

主 Agent 負責檢驗方案邊界、凍結中立簡報、仲裁衝突並做最後收斂。6 個法定席位以彼此隔離的唯讀子代理平行審查，各自從固定視角施加壓力測試。子代理的工具呼叫逐筆稽核，輸出採固定結構與六維評分；未產出合格報告的席位標示為缺席，主 Agent 不得代寫。

適用情境：技術架構重構、重大硬體採購、策略佈局、資產配置等高風險的複雜決策。

目前版本：**v2.3.1**

## 六大審查席位

| 席位 | 角色 | 專攻 |
|---|---|---|
| 1 | 極端保守派 (The Paranoiac) | 生存下限、最壞情況、黑天鵝、安全邊際 |
| 2 | 進取擴張者 (The Maximalist) | 天花板上限、機會成本、擴展爆發力 |
| 3 | 極簡實踐者 (The Pragmatist) | 執行摩擦、80/20 替代解、心智負擔 |
| 4 | 二階效應分析師 (The Systems Modeler) | 長期連鎖反應、反饋循環、非預期副作用 |
| 5 | 逆向破局者 (The Contrarian) | 推翻核心假設、相反路徑、非主流破局 |
| 6 | 人因審查員 (The Behavioral Auditor) | 人性怠惰、紀律損耗、疲勞下的有效性 |

## 流程概要

0. **檢驗閘門**：核心邊界與約束不明確時先向使用者提問，不派發任何子代理。
0.5. **建立審查員子代理類型**：關閉寫入、派生與 MCP 工具。
1. **凍結簡報**：去除使用者立場，產出 6 席共用的中立簡報。
2. **平行派發**：6 席全員派發，不得刪減或合併。
2.5. **隔離稽核**：以 `scripts/audit_council.py` 機械式檢查隔離、派發一致性與輸出格式。
3. **衝突提煉**：整理各席位之間的衝突點。
4. **仲裁與收斂**：產出加固後的方案與待核准行動。最終決策權屬於使用者。

完整規則見 [`SKILL.md`](SKILL.md)。

## 安裝

將本 repo clone 到 agy 的 skills 目錄：

```bash
# macOS / Linux
git clone https://github.com/SKR416/agentic-council.git ~/.gemini/config/skills/agentic-council
```

```powershell
# Windows
git clone https://github.com/SKR416/agentic-council.git "$env:USERPROFILE\.gemini\config\skills\agentic-council"
```

需求：

- 稽核腳本需要 Python 3（只使用標準函式庫）。找不到 Python 時，步驟 2.5 會改為人工稽核，並在執行狀態列標註。
- skills 目錄中不可有其他 `name: agentic-council` 的 SKILL.md，否則會互相遮蔽。

## 檔案結構

```
├── SKILL.md                         # 流程、閘門與規則
├── roles/                           # 6 張固定角色卡（派發時逐字附加）
├── schemas/subagent_output.md       # 子代理輸出規格與六維評分定義
├── scripts/audit_council.py         # 機械式稽核腳本
└── templates/                       # 審查員系統指令、凍結簡報、立場紀錄、儀表板規範與範本
```

每次執行的工作檔案會寫在工作區的 `.council/runs/<YYYYMMDD-HHMMSS>/`。

## 授權

[MIT](LICENSE)
