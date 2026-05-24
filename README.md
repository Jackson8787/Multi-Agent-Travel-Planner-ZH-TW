# Multi-Agent Travel Planner 🌍

> 基於大型語言模型 (LLM) 與多代理人 (Multi-Agent) 協作的智能旅遊規劃系統。

## 專案簡介 (About The Project)

**Multi-Agent Travel Planner** 是一個整合了多個專精領域 AI Agent 的自動化旅遊規劃服務。傳統的單一 LLM 往往難以在嚴格的預算、交通時間與特定偏好下，排出典型的完美行程。本專案透過「分工協作」與「自我審查」機制，將複雜的規劃任務拆解，確保最終產出的行程具備高度的可行性與客製化水準。

本專案同時整合了 LLM 觀察與評估工具 (LLM Observation & Evaluation tools)，讓開發者能清晰分析不同 AI Agent 及外部工具 (Tool Calling) 的調用狀態與執行條件。

## 核心功能與 Agent 架構 (Core Features & Architecture)

系統內部由五個核心 Agent 組成管線 (Pipeline)，彼此傳遞狀態與記憶：

* **行程 Agent (Schedule Agent):** 負責整體時間軸規劃與景點排序，確保行程流暢。
* **預算 Agent (Budget Agent):** 嚴格控管總花費，動態分配住宿、門票與餐飲開銷，防止超支。
* **交通 Agent (Transportation Agent):** 計算景點間的最佳移動方式與交通時間。
* **美食 Agent (Food Agent):** 根據使用者的自訂標籤（如：動漫、在地小吃、素食）進行精準推薦。
* **檢查 Agent (Critic Agent / Evaluator):** 擔任內部審查員。若發現「預算超支」或「行程太滿導致疲勞」，會觸發反饋循環 (Feedback Loop)，要求其他 Agent 重新調整方案。
