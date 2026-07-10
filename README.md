# Multi-Agent Travel Planner

多代理人旅遊規劃系統。LLM 負責提出行程、餐飲與品質建議；Google Places、Google Routes、匯率 API 與確定性規則負責驗證地點、動線、費用與旅遊節奏。當行程過趕、價格不完整或預算超支時，系統會暫停並讓使用者決定是否重排、調整條件或接受警告。

## 主要功能

- 多城市與多日行程規劃，先建立城市層級 macro plan，再逐日完成細部行程。
- 行程、美食與檢查 Agent 分工，使用結構化輸出降低解析錯誤。
- Google Places API (New) 驗證景點、餐廳與住宿的正式 place ID。
- Google Routes API 驗證逐段路線；自動模式在大眾運輸無結果時提供駕車估算並顯示警告。
- 四級旅遊節奏、交通方式與步行偏好，搭配確定性 pace gate。
- 預算、價格區間與 JPY/TWD 匯率快照，保留每筆資料來源。
- Human-in-the-loop：處理節奏衝突、預算衝突、重複景點與人工覆核。
- 右側地圖預覽、跨日完成狀態、完整行程摘要與 PDF 下載。
- Langfuse trace 支援 Agent 呼叫、重試、決策與評估觀測。

## 系統流程

```text
使用者需求
  -> 需求解析與 macro plan
  -> 住宿與必去景點確認
  -> 行程 Agent 產生每日候補
  -> Places grounding
  -> Routes / pace 驗證
  -> 美食 Agent 與完整路線重驗
  -> Budget gate
  -> 檢查 Agent
  -> 使用者決策或核准日程
  -> 下一天 / 完整行程 PDF
```

完整架構說明可參考 [System_Architecture_Update.pdf](System_Architecture_Update.pdf) 與 [設計文件](docs/superpowers/specs/2026-05-24-multi-agent-travel-planner-superpowers-design.md)。

## 使用的服務

| 用途 | Provider | 說明 |
|---|---|---|
| Agent 推理 | Azure OpenAI | 預設部署可使用 `gpt-5-mini` |
| 地點驗證 | Google Places API (New) | 景點、餐廳、住宿與 place ID |
| 路線驗證 | Google Routes API | 大眾運輸、駕車估算、距離與 polyline |
| 匯率換算 | ExchangeRate-API | 產生帶時間戳的預算匯率快照 |
| Trace / Evaluation | Langfuse | 選配；不影響核心規劃流程 |

API 來源、用途與限制集中維護在 `src/travel_planner/integrations/api_registry.py`。API key 只放在本機 `.env`，不可提交到 Git。

## 快速啟動

需求：macOS / Linux / Windows、可連線網路。啟動腳本會透過 `uv` 準備 Python 3.12 與依賴。

1. Clone 專案並進入資料夾。

2. 建立本機環境設定：

```bash
cp .env.example .env
```

3. 填入 `.env`：

```dotenv
GOOGLE_MAPS_API_KEY=
EXCHANGE_RATE_API_KEY=
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT=gpt-5-mini
AZURE_OPENAI_API_VERSION=2024-10-21

# Optional
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

4. 啟動：

macOS / Linux：

```bash
./run.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

預設網址為 [http://localhost:8502](http://localhost:8502)。可用 `bash run.sh --port 8503` 指定其他 port。

已有 `.venv` 時可使用：

```bash
./run_quick.sh
```

## 手動安裝

```bash
uv sync --extra dev
uv run streamlit run src/travel_planner/ui/app.py --server.port 8502
```

## 驗證

確定性測試：

```bash
uv run pytest -q
```

程式碼品質：

```bash
uv run ruff check src tests README.md
```

填妥 `.env` 後執行真實 API smoke tests：

```bash
set -a; source .env; set +a
uv run pytest -m live_api tests/live -q
```

## 資料可信度與限制

- LLM 建議不會直接視為事實；景點與餐廳必須經 Places grounding。
- Google Routes 的大眾運輸路線與票價不是每一段都有資料；fallback 結果會明確標示。
- Google Places 的餐廳價格通常是級距，不代表實際結帳金額。
- 住宿與門票精確價格可由使用者依官方或訂房來源確認後輸入。
- 匯率換算屬預算估算，不等於現金換匯或信用卡最終請款金額。
- `.env`、`.streamlit/secrets.toml`、虛擬環境與本機 worktree 已由 `.gitignore` 排除。

## 專案結構

```text
src/travel_planner/
  agents/          Agent runner 與 skill prompts
  domain/          TripSpec、日程、價格、路線與決策模型
  integrations/    Places、Routes、匯率與 API registry
  observability/   Langfuse tracing
  ui/              Streamlit UI、地圖與 PDF
  validation/      預算與旅遊節奏規則
  workflow/        Orchestrator、衝突 gate 與跨日狀態
tests/              單元、整合、workflow 與 live smoke tests
docs/               架構、規格與實作計畫
```

## 安全提醒

若 API key 或 GitHub token 曾貼到聊天、Issue、commit 或公開頁面，請立刻撤銷並重新產生。不要把 secret 寫進 README、程式碼、測試資料或 Git 歷史。
