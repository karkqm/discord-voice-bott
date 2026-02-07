@echo off
setlocal enabledelayedexpansion

:: Загружаем ключ из .env
for /f "tokens=1,2 delims==" %%a in (.env) do (
    if "%%a"=="OPENAI_API_KEY" set API_KEY=%%b
    if "%%a"=="OPENAI_BASE_URL" set BASE_URL=%%b
    if "%%a"=="LLM_MODEL" set MODEL=%%b
)

if "%BASE_URL%"=="" set BASE_URL=https://neuroapi.host/v1
if "%MODEL%"=="" set MODEL=gemini-2.5-flash-lite

echo API: %BASE_URL%
echo Model: %MODEL%
echo Key: %API_KEY:~0,8%...
echo.

echo === Test 1: Single request ===
echo %TIME%
curl -s -w "\nHTTP %{http_code} | Time: %{time_total}s\n" "%BASE_URL%/chat/completions" -H "Content-Type: application/json" -H "Authorization: Bearer %API_KEY%" -d "{\"model\":\"%MODEL%\",\"messages\":[{\"role\":\"user\",\"content\":\"Привет, как дела?\"}],\"max_tokens\":50}"
echo.

echo === Test 2: Second request (no pause) ===
echo %TIME%
curl -s -w "\nHTTP %{http_code} | Time: %{time_total}s\n" "%BASE_URL%/chat/completions" -H "Content-Type: application/json" -H "Authorization: Bearer %API_KEY%" -d "{\"model\":\"%MODEL%\",\"messages\":[{\"role\":\"user\",\"content\":\"Что такое 2+2?\"}],\"max_tokens\":50}"
echo.

echo === Test 3: Third request (no pause) ===
echo %TIME%
curl -s -w "\nHTTP %{http_code} | Time: %{time_total}s\n" "%BASE_URL%/chat/completions" -H "Content-Type: application/json" -H "Authorization: Bearer %API_KEY%" -d "{\"model\":\"%MODEL%\",\"messages\":[{\"role\":\"user\",\"content\":\"Скажи слово\"}],\"max_tokens\":50}"
echo.

echo === Done ===
pause
