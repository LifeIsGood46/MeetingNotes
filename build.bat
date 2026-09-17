@echo off
REM Build meetingnotes.exe (GUI) and meetingnotes-cli.exe (console/agents)
cd /d "%~dp0"
echo Building meetingnotes.exe (GUI) ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name meetingnotes ^
  --distpath . ^
  --add-data "meetingnotes\webapp\templates;meetingnotes\webapp\templates" ^
  --add-data "meetingnotes\webapp\static;meetingnotes\webapp\static" ^
  --add-data "meetingnotes\profiles;meetingnotes\profiles" ^
  --collect-data faster_whisper ^
  --collect-binaries ctranslate2 ^
  --collect-all nvidia.cublas ^
  --collect-all nvidia.cuda_nvrtc ^
  launcher.py
if errorlevel 1 (
  echo GUI build FAILED.
  pause
  exit /b 1
)

echo Building meetingnotes-cli.exe (console) ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --console --name meetingnotes-cli ^
  --distpath . ^
  --add-data "meetingnotes\profiles;meetingnotes\profiles" ^
  --collect-data faster_whisper ^
  --collect-binaries ctranslate2 ^
  --collect-all nvidia.cublas ^
  --collect-all nvidia.cuda_nvrtc ^
  cli_entry.py
if errorlevel 1 (
  echo CLI build FAILED.
  pause
  exit /b 1
)

echo.
echo Done: meetingnotes.exe + meetingnotes-cli.exe
endlocal
