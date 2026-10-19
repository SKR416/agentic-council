# agentic-council

**English** | [繁體中文](README.md)

A **multi-agent decision review skill** for [Antigravity](https://antigravity.google/) (agy, the Antigravity CLI), built on an Orchestrator-Subagent architecture.

The main agent checks the plan's boundaries, freezes a neutral brief, arbitrates conflicts, and makes the final synthesis. Six mandatory seats review the plan in parallel as isolated, read-only subagents, each stress-testing it from a fixed perspective. Every tool call a subagent makes is audited, outputs follow a fixed structure with six-dimension scoring, and a seat that does not produce a valid report is marked absent. The main agent may not write it on that seat's behalf.

Use it for high-stakes, complex decisions: technical architecture refactoring, major hardware purchases, strategic moves, asset allocation, and hardening itineraries and plans.

Current version: **v2.5.0**

> **Language note:** The skill's role cards, output schema, and reports are all written in Traditional Chinese, so review reports are produced in Chinese. You can submit a plan in English, but the reports and the dashboard text will be in Chinese. Report field markers and verdict values in this document are shown in English with the original Chinese in parentheses on first use.

![Dashboard overview: conclusion summary, stance distribution, score divergence](docs/images/dashboard-top.png)

> The screenshot above uses the template's built-in demo data, not a real review result.

---

## Table of Contents

- [Why It Exists](#why-it-exists)
- [Features](#features)
- [The Six Review Seats](#the-six-review-seats)
- [Overall Flow](#overall-flow)
- [Isolation Architecture](#isolation-architecture)
- [Seat Lifecycle: Audit, Retry, and Absence](#seat-lifecycle-audit-retry-and-absence)
- [Security Boundaries](#security-boundaries)
- [Visual Dashboard](#visual-dashboard)
- [Installation](#installation)
- [Usage](#usage)
- [File Structure](#file-structure) (including development and testing)
- [What Each Run Leaves Behind](#what-each-run-leaves-behind)
- [Changelog](#changelog)
- [License](#license)

---

## Why It Exists

Asking an AI "is this plan any good?" directly tends to run into three problems:

1. **It follows your tone.** If you write "I think A is better," the answer leans toward A.
2. **One perspective posing as several.** If the same conversation takes turns playing the "conservative" and the "aggressive" voice, they share the same context and the same inclination. They are repeated echoes of one bias.
3. **You cannot tell which judgments are independent.** When several opinions mention the same risk, you cannot tell whether each found it on its own or whether the brief pointed it out in advance.

agentic-council treats these as engineering problems: first strip the stance from the proposal, then hand it to independent subagents that cannot communicate with each other, use scripts to verify they stayed in bounds, and finally have the main agent lay out the conflicts and arbitrate. **The final decision always belongs to the user.** The output is a recommendation and carries no authorization to execute anything.

---

## Features

### 1. Stop and ask when information is insufficient

The first step is the "intake gate": if any of the four core boundaries (goal, resources and constraints, risk floor, weighting of success measures) is missing, **no subagent is dispatched**. The main agent asks the user at most 3 questions, each with a recommended default option.

### 2. A frozen neutral brief, with a public record of what was stripped

The main agent first rewrites the user's proposal into a "Baseline Brief" that contains only facts and options, shared by all 6 seats. Evaluative statements in the proposal (for example, "Option A is clearly better") are stripped and stored in the "stance-stripping record" (`stance_record.md`). They are **never passed to any subagent**, but they are shown to the user in full at the end so you can check whether the stripping was appropriate.

Since v2.4.0, this rule explicitly covers every section of the brief (including "options evaluated and ruled out"), and adds a third self-check: compare the stripping record sentence by sentence to confirm that no stripped original sentence remains in the brief in any form.

### 3. All six seats mobilized; absent means absent

All 6 seats must be dispatched every time. They may not be cut, merged, or reduced to a subset. Any seat that fails to produce a valid report is marked 【Absent (缺席)】, its scores are shown as "—", and **the main agent may not write, fill in, or guess an absent seat's position**. An honest absence is safer than a fabricated attendance.

### 4. Disable what can be disabled; audit afterward what cannot

Reviewers use a dedicated `council_reviewer` subagent type: writing, spawning subagents, and MCP tools are disabled directly in the type definition. File-reading and messaging tools cannot be removed at the platform level, so they are restricted by an allowlist in the system instructions, and an audit script checks the transcript call by call.

### 5. The script has the final word

[`scripts/audit_council.py`](scripts/audit_council.py) performs mechanical checks:

- **Dispatch consistency**: whether the task a subagent actually received is composed verbatim of the brief, the role card, and the output schema, with no other text added. After those three parts, a re-dispatched seat may only receive fixed entries from `templates/retry_menu.md` (at most 2). Any other text is judged a dispatch error (派發錯誤), with no manual confirmation needed from the main agent.
- **Isolation**: whether the tool calls are limited to `search_web`, `read_url_content`, and `send_message` addressed to the main agent. The transcript path must be under that seat's conversation ID in the agy brain folder. Otherwise the seat is judged unaudited (未稽核) and the file is not read.
- **Format**: required fields, stance chosen from exactly four options, six-dimension scores as integers from 1 to 10, at least 2 must-answer responses, blind-spot annotations, [External source] entries with a URL and a search tool the seat actually called, and word count (a recommended limit; exceeding it only records a warning).
- **Statistics**: stance distribution and score divergence, counting only seats judged Passed (通過) and not excluded by `role_check`.

When the script judges a seat as failing, the main agent **may not overrule it**, and may not manually fix the report to make it pass. Only two semantic judgments (role fit and blind-spot specificity) are made by the main agent against the role card, with the result written out seat by seat. When a role does not fit, the seat is excluded through `role_check`. It can only be excluded, never rescued, and the original report is still presented.

### 6. Conclusions traceable to a specific seat

During arbitration, the "leaning comparison" must name seat numbers item by item, for example "based on Seat 3's 80/20 trimming and Seat 1's budget guardrail." Describing an argument without naming the seat does not count, because you need to be able to go back to that seat's original report and tell whether it is an independent argument or mere agreement. Every item in the main plan is also labeled with its source; items the main agent adds itself are labeled "added by main agent (主代理新增)".

The script also lists wording that 2 or more seats reused and that already appeared in the brief (`anchoring_terms`). The main agent must account for each entry: which seats used it, whether they merely reused the wording of the question, and what effect it had on the conclusion. If the list is empty, write "none".

### 7. No invented numbers

In the hardened plan, **every number may only come from user input or a seat report**. If it cannot be found in either, it may not be written. Where a number is needed, rewrite it as a verification step to be confirmed. For example, instead of "complete migration on days 4–20," write "confirm the migration start and end dates with the team, then add them to the schedule."

The script enforces this rule: after the plan is saved as `plan.md`, run `--check-plan`. Any number that appears in neither the brief nor the passed seat reports is listed as `unsourced_numbers`. It must be rewritten as a step to be confirmed and the check re-run, and the plan may be presented only when the list is empty. The reasoning is simple: a number supplied by the main agent looks just as concrete as one proposed by a seat, and the user has no way to tell which one went through independent review.

### 8. No self-endorsement of the review

The coverage statement contains only four things: the number of seats present, the absent seats and their blind-spot types, the unaudited seats, and the audit results. Any sentence that describes how complete or credible the review was ("covers all aspects," "the conclusion is robust") is forbidden. An audit can only prove that the dispatched content was compliant. It cannot prove that the 6 perspectives exhausted the problem.

### 9. Diagrams that reflect real disagreement

The dashboard is generated from a fixed template plus a script. The main agent writes only the arbitration text, does not touch the layout, and has no opportunity to transcribe scores. Because the template decides the presentation, the main agent cannot use layout to amplify or downplay any one seat's opinion. See [Visual Dashboard](#visual-dashboard) for details.

---

## The Six Review Seats

| Seat | Role | Focus |
|---|---|---|
| 1 | The Paranoiac (極端保守派) | Survival floor, worst case, black swans, safety margin |
| 2 | The Maximalist (進取擴張者) | Upside ceiling, opportunity cost, expansion potential |
| 3 | The Pragmatist (極簡實踐者) | Execution friction, 80/20 alternatives, cognitive load |
| 4 | The Systems Modeler (二階效應分析師) | Long-term chain reactions, feedback loops, unintended side effects |
| 5 | The Contrarian (逆向破局者) | Overturning core assumptions, opposite paths, unconventional breakthroughs |
| 6 | The Behavioral Auditor (人因審查員) | Human laziness, loss of discipline, effectiveness under fatigue |

Role cards live in [`roles/`](roles/). They are appended verbatim at dispatch and are never rewritten to fit the situation. All context relevant to the current problem goes into the brief, not the role card, so all 6 seats see exactly the same "problem" and differ only in "perspective."

Each seat's output has a fixed structure:

- **Stance**, one of four: Support to proceed (支持推進) / Proceed with conditions (有條件推進) / Require teardown and rebuild (要求推倒重構) / Strong veto (強烈否決).
- **Six-dimension scores** (1-10, higher is more favorable to the plan): survival safety, upside potential, execution feasibility, long-term stability, assumption robustness, human sustainability.
- **【Must-answer responses (必答回應)】** (new in v2.5.0): answer the role card's must-answer questions one by one as `Q<n>：<response>`, at least 2, counted by the script.
- **Strongest supporting argument**: required even when the stance is a veto. All 6 roles are essentially attackers, so if only rejection-type stances were allowed, the system would be systematically biased toward the negative, and a good plan would be written up as a disaster forecast.
- **Fatal blind spot**: annotated with likelihood, impact, and the nature of the evidence ([Fact] / [External source] / [Speculation] / [Assumption]). An [External source] tag must come with a URL and a search tool the seat actually called, otherwise the format is judged non-compliant.
- **Defensive floor**: the conditions this seat believes the plan must meet first.

Word counts are recommended limits: 1,200 words for regular seats and 1,500 for Seat 5. Exceeding them only records a warning.

---

## Overall Flow

```mermaid
flowchart TD
    U([User submits a plan]) --> G{"Step 0: Intake gate<br/>Goal / constraints / risk floor / weighting<br/>all four complete?"}
    G -- Missing --> Q["At most 3 questions<br/>each with a recommended default<br/>no subagent dispatched"]
    Q --> U
    G -- Complete --> S["Step 0.5: Define council_reviewer<br/>disable write / spawn / MCP tools<br/>confirm Python is available"]
    S --> F["Step 1: Freeze the brief<br/>brief.md shared by 6 seats<br/>stance_record.md for the user only"]
    F --> D["Step 2: Dispatch 6 seats in parallel in one call<br/>write seats.json"]
    D --> A1["Step 2.5 first pass: audit_council.py<br/>dispatch / isolation / format / statistics<br/>produces audit.json, summary.md, report_full.md"]
    A1 --> A2["Second pass: main agent judgment<br/>role fit / blind-spot specificity<br/>exclude via role_check if not met"]
    A2 --> R{"Seat valid?"}
    R -- Invalid --> RE["Re-dispatch once with a new subagent<br/>only fixed retry_menu.md entries (at most 2)"]
    RE --> A1
    R -- Still failing after retry --> AB["Mark 【Absent】<br/>not written on its behalf, excluded from statistics"]
    R -- Valid --> SY
    AB --> SY["Step 3: Conflict extraction<br/>deep contradictions / high-risk blind spots / consensus check"]
    SY --> AR["Step 4: Arbitration and synthesis<br/>leaning comparison / anchoring-term accounting / disposition table / hardened plan<br/>plan saved as plan.md, --check-plan unsourced numbers must be empty<br/>circuit-breaker conditions / action list"]
    AR --> DB["HTML dashboard generated by default<br/>build_dashboard.py"]
    AR --> OUT([User makes the final decision])
    DB --> OUT
```

| Step | What happens | Key rules |
|---|---|---|
| 0 | Intake gate | Stop if a boundary is missing; do not dispatch |
| 0.5 | Create the reviewer subagent type | Redefined on every run, not relying on earlier setup; if the environment does not support subagents, stop. The main agent may not play 6 seats in a single context |
| 1 | Freeze the brief | The brief is verbatim identical for all 6 seats; it may not contain evaluation, recommendation, or prejudgment |
| 2 | Parallel dispatch | All 6 seats, in a single call; `seats.json` records each seat's transcript; after dispatch, output one sentence and end the turn |
| 2.5 | Isolation audit and result verification | The script's verdict is final; only one re-dispatch, with only fixed `retry_menu.md` entries; statistics count only seats that are Passed and not excluded by `role_check` |
| 3 | Conflict extraction | Use the script's stance distribution and score divergence directly, with no recalculation; every `anchoring_terms` entry must be accounted for |
| 4 | Arbitration and synthesis | Numbers in the plan must have a source; save as `plan.md` and run `--check-plan`; the coverage statement may not carry an overall assessment; the dashboard is generated by default |

---

## Isolation Architecture

Isolation is the premise of the whole design. **Any inclination that exists before dispatch, whether it comes from the main agent, the user's wording, or global rules, seeps into all 6 seats at once**, and the 6 reports are no longer independent.

```mermaid
flowchart LR
    subgraph MAIN["Main agent"]
        direction TB
        UP["User proposal<br/>with stance and evaluation"]
        GR["Global development rules"]
        BR["brief.md<br/>neutral brief"]
        SR["stance_record.md<br/>stripped original sentences"]
        UP --> BR
        UP --> SR
    end

    subgraph SEAT["Each seat's council_reviewer<br/>seats never read from or talk to each other"]
        direction TB
        P1["Reviewer system instructions"]
        P2["Frozen brief<br/>verbatim identical for all 6 seats"]
        P3["This seat's role card"]
        P4["Output schema"]
    end

    BR --> P2
    SR -. never passed .-> SEAT
    GR -. replaced by custom system instructions, not inherited .-> SEAT
    SEAT --> RPT["Report<br/>can only be returned to the main agent via send_message"]
    RPT --> AUD["audit_council.py<br/>audits the transcript call by call"]
```

| Isolation direction | How it works |
|---|---|
| Horizontal | Each seat is strictly forbidden from reading other seats' output or communicating with them. The audit script checks tool calls and message recipients. |
| Vertical | Each seat sees only "reviewer system instructions + frozen brief + fixed role card + fixed output schema." It cannot see the user's stance, the main agent's evaluation, or global rules. |
| Tools | Write, spawn, and MCP are disabled in the type definition; file reading and messaging are handled with an allowlist plus after-the-fact audit. |

> The platform cannot technically stop a subagent from reading files through absolute paths, so the file-reading restriction relies on system-instruction prohibition plus the Step 2.5 audit, not on the `Workspace` parameter.

---

## Seat Lifecycle: Audit, Retry, and Absence

```mermaid
stateDiagram-v2
    [*] --> Dispatched
    Dispatched --> ScriptAudit: Report collected
    ScriptAudit --> DispatchError: Task carries extra content<br/>or retry text is not in the menu
    DispatchError --> Dispatched: Main agent fixes and re-dispatches<br/>does not use up the retry
    ScriptAudit --> Unaudited: Transcript path is not under<br/>this seat's conversation ID
    Unaudited --> [*]: Not read, not counted in statistics
    ScriptAudit --> RoleFit: Script verdict is Passed
    ScriptAudit --> Invalid: Format non-compliant / isolation violation / timeout
    RoleFit --> Valid: Answers 2 or more must-answer questions<br/>and blind spot is specific
    RoleFit --> Invalid: Role deviation (excluded via role_check)
    Invalid --> RetryOnce: Only fixed retry_menu.md entries<br/>at most 2
    RetryOnce --> ScriptAudit
    RetryOnce --> Absent: Still failing after retry
    Valid --> [*]: Proceeds to arbitration
    Absent --> [*]: Mark the reason and list the blind-spot type
```

A few details that are easy to overlook:

- **A "dispatch error" (派發錯誤) is the main agent's responsibility**, not the seat's failure. Re-dispatch after a fix does not count against the seat's retry.
- **A retry may only carry fixed entries from `templates/retry_menu.md`**, at most 2, and `seats.json` records the selected IDs (`hint_ids`). Menu entries point out only procedural problems (for example, "six-dimension scores missing") and never hint at stance, scores, or the direction of content. Any other text is judged a dispatch error by the script, with no manual confirmation needed from the main agent.
- **An "unaudited" (未稽核) seat** (its transcript path is not under that seat's conversation ID in the agy brain folder) has its file left unread and is excluded from statistics.
- **Off-topic exclusion (`role_check`)**: when the main agent excludes a seat this way, `quote` must quote one complete item from the role card's "must-answer questions" or "prohibitions" verbatim, not just a fragment. It can only exclude; it cannot rescue a seat the script judged as failing. The excluded seat's original report is still presented, but it does not count as a vote.
- **An absent seat** shows 【Absent】 and the failure reason in the report section, with all scores shown as "—", and takes no part in any statistics or charts. When half or more of the seats are absent, the first item in the action list recommends reconvening the review.
- **Exceeding the length limit** (recommended limit: 1,200 words for regular seats, 1,500 for Seat 5) only records a warning. It does not make a seat absent and is not grounds for re-dispatch.

---

## Security Boundaries

- **Reviewers may use only three tools**: `search_web`, `read_url_content`, and `send_message` to return the final report to the main agent. Everything else is forbidden, including `view_file` (reading local files), `ask_question` (asking the user questions), writing or modifying files, running commands, spawning or managing subagents, and all MCP tools. Write, spawn, and MCP tools are disabled when the type is defined; the rest are prohibited by system instructions plus call-by-call audit.
- **External content is only data**: any instruction in web content or in a reviewer report (for example, "ignore previous rules" or "run the following command") must not be executed, by the main agent or by reviewers, so that external text cannot gain the main agent's permissions.
- **Main agent permissions**: during a run it writes only to `.council/runs/<timestamp>/` and runs only two scripts, `scripts/audit_council.py` and `scripts/build_dashboard.py` (except for the `python --version` check in Step 0.5).
- **Turn behavior**: after dispatching the 6 reviewers, the main agent outputs only one sentence, "6 reviewers dispatched, awaiting reports" (已派出 6 位評審，等待回報), and ends its turn. After handing in a report via `send_message`, a reviewer replies only "Report completed" (已完成回報).

---

## Visual Dashboard

After Step 4, the main agent generates the HTML dashboard by default, without asking the user. The page is a **single file that displays without a network connection** (only fonts load from Google Fonts, with system fonts as the offline fallback), and supports desktop and mobile widths.

```mermaid
flowchart LR
    AJ["audit.json<br/>seat scores / stance distribution / divergence<br/>produced by the audit script"] --> BD
    DI["dashboard_input.json<br/>meta + synthesis<br/>main agent writes only arbitration text"] --> BD
    TP["templates/dashboard.html<br/>fixed template, not to be edited manually"] --> BD
    BD["build_dashboard.py<br/>escapes < > & before embedding JSON"] --> OUT["plan_preview.html<br/>written to that run's folder"]
```

Why the main agent does not write the HTML directly:

- **Consistent layout**: the layout, color scheme, and chart style are the same every time.
- **Scores do not pass through the main agent**: score data is brought in by the script from `audit.json`, so the main agent has no chance to transcribe it and cannot use layout to amplify or downplay any one seat.
- **Prevents a broken page**: report content may contain text from the web. The script escapes `<`, `>`, and `&`, so content containing `</script>` cannot break the whole page.

### Page contents

From top to bottom, the page contains:

| Section | Content |
|---|---|
| **Conclusion summary** (new in v2.4.0) | A label comparing against the original leaning, a one-sentence conclusion, and the first 3 circuit-breaker conditions, with a link that jumps to the full plan |
| Overview | Stance distribution bar, score divergence table |
| Six-dimension scores | 6 radar charts (absent seats show 【Absent】 and the reason), a color-scale score matrix |
| Original seat reports | Collapsed by default, with the audit verdict; re-dispatched seats are labeled "Re-dispatched" (已重派) |
| Unaudited and off-topic exclusion list | Lists seats judged unaudited and seats excluded by `role_check`, with reasons (these seats are excluded from statistics and charts; original reports are still presented) |
| Arbitration | Deep contradictions, high-risk blind spots, leaning comparison, coverage statement, anchoring terms (`anchoring_terms`) and unsourced numbers (`unsourced_numbers`) |
| Defensive-floor disposition table | For each seat's each floor item: adopted / partially adopted / rejected, with reasons |
| Hardened main plan | With seat source labels; shown as a timeline when time fields are present (new in v2.4.0) |
| Circuit-breaker and stop-loss conditions, immediate action list | Quantified termination indicators and the first three starting steps |

![Radar charts and score matrix, with absent seats marked by a dashed outline](docs/images/dashboard-scores.png)

![Defensive-floor disposition table and timeline main plan](docs/images/dashboard-plan.png)

In degraded mode (output produced in a single context) or during manual audit, a warning banner is displayed at the top of the page.

### Data fields for the conclusion summary and timeline

Both new fields are optional. When omitted, the page keeps its previous behavior:

```json
{
  "synthesis": {
    "conclusion": "有條件推進：不一次性全面拆分，先備妥回退機制，只抽出痛點最大的一個模組試行。",
    "plan": [
      { "time": "拆分前",   "text": "先建立可一鍵回退的切換機制並演練一次。", "sources": [1] },
      { "time": "第一階段", "text": "只選痛點最大的一個模組抽出試行。",       "sources": [3, 5] }
    ]
  }
}
```

(The sample values are Chinese because the skill's output is Chinese. In English they read: "Proceed with conditions: do not split everything at once; set up a rollback mechanism first and trial only the module with the biggest pain point." / "Before the split": "First build a one-click rollback switch and rehearse it once." / "Phase 1": "Extract and trial only the module with the biggest pain point.")

- `conclusion`: the one-sentence version of the Step 4 arbitration conclusion. It must be excerpted verbatim from the conclusion already written and may not add new judgments. When omitted, the conclusion card uses the explanatory text of the leaning comparison instead.
- `plan[].time`: a time or phase (for example "11:45", "16:40–19:15", "before the split"). As soon as any item has it, the main plan switches to the timeline layout, with times shown in the left column. For itinerary-type topics, filling it in for every item is recommended.

See [`templates/dashboard.md`](templates/dashboard.md) for the full field specification.

---

## Installation

Clone this repo into agy's skills directory:

```bash
# macOS / Linux
git clone https://github.com/SKR416/agentic-council.git ~/.gemini/config/skills/agentic-council
```

```powershell
# Windows
git clone https://github.com/SKR416/agentic-council.git "$env:USERPROFILE\.gemini\config\skills\agentic-council"
```

Requirements:

- The audit script and the dashboard script require Python 3 (standard library only). If Python cannot be found, Step 2.5 falls back to manual audit, notes it in the run status line, and no dashboard is generated.
- The skills directory must not contain any other SKILL.md with `name: agentic-council`, otherwise they will shadow each other.
- The runtime environment must support `define_subagent` and `invoke_subagent`. If it does not, the flow stops and tells you; it will not quietly play 6 seats in a single context.

---

## Usage

After installation, describe the decision you want reviewed in an agy conversation and name agentic-council, for example:

> I plan to split our monolith into microservices. Please review it with agentic-council.

The flow then goes as follows:

1. If the main agent finds the boundaries incomplete, it first asks you at most 3 questions, each with a recommended default you can simply accept.
2. Once the boundaries are complete, the main agent freezes the brief and dispatches the 6 seats. You do not need to step in during this time.
3. When it finishes, you get in the conversation: the run status line and audit table, the review summary from `summary.md`, the arbitration, the hardened plan, and the path of the dashboard (generated by default). Full reports are not pasted into the conversation; file paths are provided for you to consult: `report_full.md` (the 6 full reports and the full audit table), `brief.md`, `stance_record.md` (so you can check whether the stripping was appropriate), and `audit.json`.
4. The plan is a recommendation. **The final decision and any action taken are up to you.**

Tips:

- You can freely state your leaning in your proposal, and the main agent will strip it. But state facts and constraints clearly: the brief includes only what you explicitly provide, and the main agent may not add background knowledge on its own.
- Revising the plan midway counts as a new review round: the brief is frozen again and all 6 seats are re-dispatched.
- Seat reports connect to web search, so expect the review to take some time.
- The skill does not pin a model: all 6 reviewers use whatever model agy is currently configured with, usually the same one. Their independence comes from isolated contexts and fixed role cards, not from model diversity.

---

## File Structure

```
├── SKILL.md                         # Flow, gates, and rules (the main file agy reads)
├── roles/                           # 6 fixed role cards (appended verbatim at dispatch)
├── schemas/
│   ├── subagent_output.md           # Subagent output schema and six-dimension score definitions
│   └── run_files.md                 # Run folder file contract (seats.json, audit.json, etc.)
├── scripts/
│   ├── audit_council.py             # Mechanical audit script
│   ├── build_dashboard.py           # Dashboard generation script
│   └── transcripts/agy.py           # agy transcript reader
├── templates/
│   ├── reviewer_system_prompt.md    # System instructions for the reviewer subagent type
│   ├── retry_menu.md                # Fixed hint menu for re-dispatch
│   ├── preset_menu.md               # Recommended option menu
│   ├── baseline_brief.md            # Frozen brief template (passed to subagents)
│   ├── stance_record.md             # Stance-stripping record template (must not be passed to subagents)
│   ├── dashboard.md                 # Dashboard data fields and generation spec
│   └── dashboard.html               # Fixed dashboard template (with demo data)
├── tests/                           # pytest (python -m pytest tests -q)
└── docs/images/                     # Example screenshots used by the README
```

### Development and testing

After changing a script or contract, run this from the repo root:

```bash
python -m pytest tests -q
```

The tests cover cases such as smuggled content in re-dispatch and forged transcript paths. When you change the format in `schemas/run_files.md`, change the audit script, the dashboard script, and the tests together.

The core of the design is that SKILL.md and each template file are "verbatim identical every time": if any file fails to load, the flow stops immediately and tells you which file is missing. It may not rewrite from memory.

---

## What Each Run Leaves Behind

Each run's working files are written to `.council/runs/<YYYYMMDD-HHMMSS>/` in the workspace:

| File | Content |
|---|---|
| `brief.md` | The frozen neutral brief (shared by 6 seats, may not be modified at any point) |
| `stance_record.md` | The user's stripped stance (not passed to subagents, only for the user to inspect) |
| `seats.json` | Each seat's subagent ID and transcript path (fill in `transcript_full.jsonl`, which must be under that seat's conversation ID in the agy brain folder; re-dispatched seats are marked `retry` with the selected hint IDs; off-topic exclusion is marked `role_check`) |
| `plan.md` | Full text of the hardened main plan, used by `--check-plan` to verify number sources |
| `audit.json` | The audit script's verdicts, scores, stance distribution, and divergence, plus `anchoring_terms` and `unsourced_numbers`; the data source for the dashboard |
| `summary.md` | Review summary and audit table produced by the audit script, pasted into the conversation as is |
| `report_full.md` | Original text of the 6 full seat reports and the full audit table; not pasted into the conversation, only the path is provided |
| `dashboard_input.json` | The dashboard's `meta` and `synthesis` |
| `plan_preview.html` | The finished dashboard (generated by default) |

Each run uses its own timestamped folder, so new results never overwrite old ones.

---

## Changelog

> When updating this document, please keep the Chinese version [README.md](README.md) in sync.

### v2.5.0

Fixes based on the results of a Claude × Gemini cross-review. The focus is to have the script, rather than self-discipline, determine what the main agent can smuggle through.

- **Retries cannot carry hints**: a re-dispatched task may only carry fixed entries from `templates/retry_menu.md` (at most 2), and `seats.json` records the selected IDs. Any other text is judged a dispatch error (派發錯誤).
- **Records cannot be forged**: the transcript must be under that seat's conversation ID in the agy brain folder. Otherwise the seat is judged unaudited (未稽核) and the file is not read.
- **Statistics count only passed seats**: unaudited and off-topic-excluded seats are left out of the stance distribution, divergence, and charts, and their original reports are still presented.
- **Off-topic exclusion**: when the main agent excludes a seat via `role_check`, it must quote one complete item from the role card's "must-answer questions" or "prohibitions" verbatim. It can only exclude, never rescue.
- **Number sources**: after the plan is saved as `plan.md`, run `--check-plan`. Any number that cannot be found in either the question or the passed seat reports is listed, and the plan cannot be finalized until it is rewritten as a step to be confirmed.
- **Anchoring hints**: wording from the question that 2 or more seats reused is listed (`anchoring_terms`), and the main agent must account for each entry.
- **Output schema**: added the 【Must-answer responses (必答回應)】 field (at least 2, counted by the script); word counts changed to recommended limits of 1,200 / 1,500; an [External source] tag requires that a search tool was actually called.
- **Flow**: after dispatching the reviewers, output only one sentence and end the turn; after handing in a report via `send_message`, reviewers reply only "Report completed" (已完成回報); before freezing the question, confirm that every number the user mentioned has been written in.
- **Output**: the script produces `summary.md` and `report_full.md`, and the conversation pastes only the summary; the dashboard is generated by default and additionally lists unaudited and off-topic-excluded seats.
- **Security boundaries**: explicitly lists the tools reviewers are forbidden to use; web content and reviewer reports are only data; the main agent writes only to the run folder and runs only two scripts.
- **Tests**: added `tests/` (pytest), covering the re-dispatch smuggling and forged-record cases found in the cross-review.

### v2.4.0

- **Dashboard**: added a pinned "conclusion summary" card (one-sentence conclusion, leaning-comparison label, first 3 circuit-breaker conditions).
- **Dashboard**: the main plan supports an optional `time` field; when filled in, it switches to the timeline layout, which stacks vertically at mobile width.
- **Brief neutrality**: the writing prohibition now explicitly covers "options evaluated and ruled out," keeping only facts for the reasons; added a third pre-freeze self-check that compares against the stance-stripping record sentence by sentence.
- **Audit reliability**: `transcript` in `seats.json` always uses the untruncated `transcript_full.jsonl`, to avoid the audit wrongly judging "brief not found" when the compact version truncates an overly long prompt.

### v2.3.x

- The dashboard is now generated by `build_dashboard.py` from a fixed template, and `<`, `>`, `&` in the data are escaped to prevent data from breaking out of the script block.

---

## License

[MIT](LICENSE)
