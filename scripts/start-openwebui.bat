@echo off
rem ===================================================================
rem  start-openwebui.bat —— 启动 open-webui（本地模型，不联网）
rem
rem  🔴 关键配置说明（2026-11-26 踩的坑）：
rem
rem  默认设置下 open-webui 会去 HuggingFace 下载嵌入模型
rem  sentence-transformers/all-MiniLM-L6-v2，但本机访问不了 HF：
rem      [WinError 10060] 连接尝试失败
rem      thrown while requesting HEAD https://huggingface.co/...
rem  卡在 "Waiting for application startup." 永远起不来。
rem
rem  修法：用本机已有的中文嵌入模型 BAAI/bge-base-zh-v1.5
rem  （C:\Users\23575\.cache\huggingface\hub 里已有 780 MB）
rem  中文场景比 MiniLM 更合适。
rem ===================================================================

set "DATA_DIR=E:\openwebui-data"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

rem --- 连本地 Ollama ---
set "OLLAMA_BASE_URL=http://127.0.0.1:11434"

rem --- 🔴 嵌入模型：用本地缓存，禁止联网下载 ---
set "RAG_EMBEDDING_ENGINE="
set "RAG_EMBEDDING_MODEL=BAAI/bge-base-zh-v1.5"
set "RAG_EMBEDDING_MODEL_AUTO_UPDATE=False"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"

rem --- 免登录（内部演示）---
set "WEBUI_AUTH=False"
set "ENABLE_SIGNUP=False"

rem --- 默认模型 ---
set "DEFAULT_MODELS=route-v6-q8"
set "DEFAULT_LOCALE=zh-CN"

rem --- 关掉不需要的生成类功能，减少模型调用 ---
set "ENABLE_EVALUATION_ARENA_MODELS=False"
set "ENABLE_COMMUNITY_SHARING=False"
set "ENABLE_MESSAGE_RATING=False"
set "ENABLE_AUTOCOMPLETE_GENERATION=False"
set "ENABLE_TAGS_GENERATION=False"
set "ENABLE_TITLE_GENERATION=False"
set "ENABLE_OPENAI_API=False"

cd /d "E:\openwebui"

echo.
echo   open-webui  http://127.0.0.1:8080
echo   DATA_DIR   = %DATA_DIR%
echo   OLLAMA     = %OLLAMA_BASE_URL%
echo   MODEL      = %DEFAULT_MODELS%
echo   EMBEDDING  = %RAG_EMBEDDING_MODEL%  (offline)
echo.

"E:\openwebui\Scripts\open-webui.exe" serve --host 127.0.0.1 --port 8080
