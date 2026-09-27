@echo off
REM setup.bat - one-time setup for Windows
REM Creates a virtual environment and installs dependencies.

echo Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo.
    echo "python" was not found. Try running this with "py" instead:
    echo     py -m venv venv
    pause
    exit /b 1
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt

echo.
echo Setup complete. Run start.bat to launch the app.
pause
