@echo off
REM start.bat - activates the virtual environment and runs the app.
REM Run setup.bat first if you haven't already.

if not exist venv (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
echo Starting Waterfront Mall Parking on http://localhost:5000 ...
python app.py
pause
