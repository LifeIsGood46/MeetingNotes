@echo off
REM Build portable MeetingNotes folder: onedir (no self-extraction),
REM both exes sharing one runtime, all app data beside the exes.
cd /d "%~dp0"
set FOLDER=MeetingNotes

echo Building GUI exe (onedir) ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name meetingnotes ^
  --icon "assets\logo.ico" ^
  --distpath dist ^
  --workpath build ^
  --add-data "meetingnotes\webapp\templates;meetingnotes\webapp\templates" ^
  --add-data "meetingnotes\webapp\static;meetingnotes\webapp\static" ^
  --add-data "meetingnotes\profiles;meetingnotes\profiles" ^
  --collect-data faster_whisper ^
  --collect-binaries ctranslate2 ^
  --collect-all nvidia.cublas ^
  --collect-all nvidia.cuda_nvrtc ^
  --hidden-import clr --collect-all webview.platforms.edgechromium --collect-all webview.platforms.winforms ^
  launcher.py
if errorlevel 1 (
  echo GUI build FAILED.
  pause
  exit /b 1
)

echo Building CLI exe into the same folder ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --console --name meetingnotes-cli ^
  --icon "assets\logo.ico" ^
  --distpath dist ^
  --workpath build ^
  --paths "dist\%FOLDER%\_internal" ^
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

echo Assembling portable folder ...
REM Both exes live in dist\MeetingNotes\ side by side, sharing _internal.
if not exist "dist\%FOLDER%\meetingnotes-cli.exe" move "dist\meetingnotes-cli\meetingnotes-cli.exe" "dist\%FOLDER%\" >nul
robocopy "dist\meetingnotes-cli\_internal" "dist\%FOLDER%\_internal" /E /NFL /NDL /NJH /NJS >nul
rd /s /q "dist\meetingnotes-cli" 2>nul
REM Portable layout skeleton (app fills the rest on first run).
mkdir "dist\%FOLDER%\results" 2>nul

echo Zipping release package ...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\%FOLDER%\*' -DestinationPath 'dist\MeetingNotes-0.2.0-win64.zip' -Force"

echo.
echo Done: dist\%FOLDER%\ (run meetingnotes.exe) + dist\MeetingNotes-0.2.0-win64.zip
endlocal
