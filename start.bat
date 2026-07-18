@echo off
REM =============================================================================
REM  Sentinel — Windows Startup Script
REM  Run this from the sentinel\ project root (where this file lives).
REM  It will:
REM    1. Optionally create a venv if none exists
REM    2. Install/verify dependencies
REM    3. Start the FastAPI server on port 8000 (background)
REM    4. Start the dashboard on port 8080 (background)
REM    5. Open your browser automatically
REM =============================================================================

cd /d "%~dp0"
echo.
echo  ====================================================
echo   Sentinel - AI Guardrail Agent
echo  ====================================================
echo.

REM ---- Step 1: Check Python -------------------------------------------------------
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)
echo [OK] Python found.

REM ---- Step 2: Create/activate venv if needed ------------------------------------
if not exist "venv\Scripts\activate.bat" (
    echo [SETUP] Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
)

echo [OK] Activating virtual environment...
call venv\Scripts\activate.bat

REM ---- Step 3: Install dependencies -----------------------------------------------
echo [SETUP] Checking / installing dependencies...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] pip install failed. Check your internet connection and requirements.txt.
    pause
    exit /b 1
)
echo [OK] Dependencies ready.

REM ---- Step 4: Check for trained model artifacts ----------------------------------
if not exist "sentinel_core\model_artifacts\model.pkl" (
    echo [SETUP] model.pkl not found — training classifier from scratch...
    python train\train_classifier.py
    if %errorlevel% neq 0 (
        echo [WARNING] Classifier training failed. Stage 2 will be skipped, Stage 3 LLM will handle all ambiguous actions.
    ) else (
        echo [OK] Classifier trained successfully.
    )
) else (
    echo [OK] Classifier model artifacts found.
)

REM ---- Step 5: Start the FastAPI API server in a new window -----------------------
echo [START] Starting Sentinel API server on http://localhost:8000 ...
start "Sentinel API (port 8000)" cmd /k "cd /d "%~dp0" && venv\Scripts\python.exe -m uvicorn api.main:app --port 8000 --reload"

REM Wait a moment for the API to start before opening the dashboard
echo [WAIT]  Waiting 4 seconds for API to come up...
timeout /t 4 /nobreak >nul

REM ---- Step 6: Start the dashboard HTTP server in a new window --------------------
echo [START] Starting dashboard server on http://localhost:8080 ...
start "Sentinel Dashboard (port 8080)" cmd /k "cd /d "%~dp0\dashboard" && python -m http.server 8080"

REM Wait a moment for the dashboard server to start
timeout /t 2 /nobreak >nul

REM ---- Step 7: Open browser -------------------------------------------------------
echo [START] Opening dashboard in your browser...
start "" "http://localhost:8080"

REM ---- Step 8: Start the SSE MCP server in a new window --------------------------
echo [START] Starting Sentinel SSE MCP server on http://localhost:8002 ...
start "Sentinel SSE MCP (port 8002)" cmd /k "cd /d "%~dp0" && venv\Scripts\python.exe mcp_server\sse_server.py"

echo.
echo  ====================================================
echo   Sentinel is running!
echo.
echo   Dashboard       : http://localhost:8080
echo   API             : http://localhost:8000
echo   API Docs        : http://localhost:8000/docs
echo   API Health      : http://localhost:8000/health
echo   SSE MCP (web)   : http://localhost:8002/sse
echo.
echo   Three terminal windows were opened:
echo     - "Sentinel API (port 8000)"      <- REST backend
echo     - "Sentinel Dashboard (port 8080)" <- static file server
echo     - "Sentinel SSE MCP (port 8002)"  <- web-based MCP endpoint
echo.
echo   Stdio MCP (local tools like Cursor, Claude Code):
echo     python mcp_server\server.py
echo.
echo   Test the SSE endpoint with MCP Inspector:
echo     npx -y @modelcontextprotocol/inspector sse http://localhost:8002/sse
echo.
echo   Internet: only needed if Stage 3 is set to an API provider.
echo   Local Ollama model = fully offline.
echo  ====================================================
echo.
pause
