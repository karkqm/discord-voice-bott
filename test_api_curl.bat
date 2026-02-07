@echo off
setlocal enabledelayedexpansion

:: Загружаем ключ из .env
for /f "tokens=1,2 delims==" %%a in (.env) do (
    if "%%a"=="OPENAI_API_KEY" set "API_KEY=%%b"
    if "%%a"=="OPENAI_BASE_URL" set "BASE_URL=%%b"
    if "%%a"=="LLM_MODEL" set "MODEL=%%b"
)

if "!BASE_URL!"=="" set "BASE_URL=https://neuroapi.host/v1"
if "!MODEL!"=="" set "MODEL=gemini-2.5-flash-lite"

set "URL=!BASE_URL!/chat/completions"

echo API: !URL!
echo Model: !MODEL!
echo Key: !API_KEY:~0,8!...
echo.

echo === Test 1: Single request ===
echo !TIME!
curl -s -w "HTTP %%{http_code} | Time: %%{time_total}s" "!URL!" -H "Content-Type: application/json" -H "Authorization: Bearer !API_KEY!" -d "{\"model\":\"!MODEL!\",\"messages\":[{\"role\":\"user\",\"content\":\"Hi\"}],\"max_tokens\":50}"
echo.
echo.

echo === Test 2: Second request (no pause) ===
echo !TIME!
curl -s -w "HTTP %%{http_code} | Time: %%{time_total}s" "!URL!" -H "Content-Type: application/json" -H "Authorization: Bearer !API_KEY!" -d "{\"model\":\"!MODEL!\",\"messages\":[{\"role\":\"user\",\"content\":\"2+2?\"}],\"max_tokens\":50}"
echo.
echo.

echo === Test 3: Third request (no pause) ===
echo !TIME!
curl -s -w "HTTP %%{http_code} | Time: %%{time_total}s" "!URL!" -H "Content-Type: application/json" -H "Authorization: Bearer !API_KEY!" -d "{\"model\":\"!MODEL!\",\"messages\":[{\"role\":\"user\",\"content\":\"Say ok\"}],\"max_tokens\":50}"
echo.
echo.

echo === Done ===
pause
