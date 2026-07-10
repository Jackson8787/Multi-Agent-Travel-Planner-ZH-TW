# Multi-Agent Travel Planner Agent Handoff

Last updated: 2026-07-10

這份文件提供給下一位接手本專案的開發者或 Agent。GitHub repository：
`https://github.com/Rlonglong/Multi-Agent-Travel-Planner`

## 目前狀態

- 主要分支：`main`
- Python：3.12
- 套件與環境：`uv`、`uv.lock`
- Web UI：Streamlit
- 完整依賴可由根目錄 `run.sh` / `run.ps1` 自動準備。
- `.env`、`.streamlit/secrets.toml`、`.venv`、`.worktrees` 均不得提交。

## 已完成功能

- 文字需求解析與多城市 macro plan。
- 各城市住宿候補、手動住宿選擇與地圖預覽。
- 行程、美食、檢查 Agent 的結構化輸出。
- 必去景點鎖定、跨日已訪景點追蹤與重複景點決策。
- Google Places grounding 與逐段 Google Routes 驗證。
- `TRANSIT`、`DRIVE`、`AUTO` 路線模式；AUTO 可 fallback 至駕車估算並在 UI 顯示警告。
- 四級旅遊節奏與 deterministic pace gate。
- `NORMAL`、`PREFER_WALKING`、`SHORT_WALK_ONLY` 步行偏好，已接入確定性驗證。
- 預算、價格區間、JPY/TWD 匯率快照與來源證據。
- 節奏、預算、價格缺漏、人工覆核等 human-in-the-loop 決策。
- 多日進度、已完成日程摘要、返回前一天與繼續下一天。
- 完整行程 PDF 下載。
- Langfuse tracing 與 live API smoke tests。

## 啟動方式

```bash
cp .env.example .env
# 填入必要 API credentials
./run.sh
```

預設網址：`http://localhost:8502`

已有 `.venv` 時：

```bash
./run_quick.sh
```

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

## 驗證方式

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests README.md
```

真實 API：

```bash
set -a; source .env; set +a
uv run pytest -m live_api tests/live -q
```

2026-07-10 最後驗證結果：

- deterministic tests：`125 passed, 5 skipped`
- live API smoke tests：`5 passed`
- Ruff：passed
- Streamlit startup：`HTTP 200` on port 8502

## 主要程式位置

- `src/travel_planner/ui/app.py`：Streamlit UI、對話流程、決策與跨日呈現。
- `src/travel_planner/ui/map_component.py`：預覽地圖與已驗證路線。
- `src/travel_planner/ui/pdf_generator.py`：完整行程 PDF。
- `src/travel_planner/workflow/orchestrator.py`：macro/micro workflow、gate、重試與跨日狀態。
- `src/travel_planner/agents/runner.py`：Azure OpenAI 結構化 Agent runner。
- `src/travel_planner/domain/models.py`：TripSpec、路線、價格、時段與決策模型。
- `src/travel_planner/integrations/`：Places、Routes、匯率與 provider registry。
- `src/travel_planner/validation/`：預算與 pace 規則。
- `tests/`：domain、integration、workflow、UI 與 live tests。

## 已知限制

- Google Routes 不保證所有大眾運輸路段都有結果或票價；AUTO fallback 僅為估算。
- Google Places 的餐廳價格通常是級距，不是最終消費金額。
- 住宿與門票精確價格仍可能需要使用者依官方來源確認。
- 匯率換算是預算快照，不代表刷卡或現金換匯成交價。
- 真實 LLM/API 的輸出會受服務狀態、配額、區域與模型部署影響。
- UI 已具備 sticky map 驗證頁 `docs/verify-sticky-map.html`，正式展示前仍建議以實際瀏覽器跑一次完整流程。

## 接手建議

接手後先執行測試與啟動 smoke test，再開始功能修改。維持目前責任邊界：LLM 負責提案與解釋，外部 API 負責事實驗證，程式規則負責預算與節奏判定。新增 API 時，來源、用途與限制應同步登記於 `api_registry.py`，secret 只能放在 `.env`。
