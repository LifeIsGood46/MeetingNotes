@echo off
REM Build portable MeetingNotes folder: onedir (no self-extraction),
REM both exes sharing one runtime, all app data beside the exes.
cd /d "%~dp0"
set FOLDER=MeetingNotes
set VENDOR=vendor
set VERSION=0.3.0

REM ---------------------------------------------------------------------------
REM Vendored binaries: ffmpeg/ffprobe (audio extraction) + WebView2 window.
REM ffmpeg ships inside the app so a clean machine needs nothing on PATH.
REM ---------------------------------------------------------------------------
if not exist "%VENDOR%\ffmpeg\ffmpeg.exe" (
  echo Downloading ffmpeg essentials build ...
  powershell -NoProfile -Command ^
    "$ErrorActionPreference='Stop';" ^
    "$u='https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip';" ^
    "$z='%VENDOR%\ffmpeg.zip';" ^
    "New-Item -ItemType Directory -Force '%VENDOR%' | Out-Null;" ^
    "Invoke-WebRequest -Uri $u -OutFile $z;" ^
    "Expand-Archive -Path $z -DestinationPath '%VENDOR%\ffmpeg-tmp' -Force;" ^
    "$bin=Get-ChildItem '%VENDOR%\ffmpeg-tmp' -Recurse -Filter ffmpeg.exe | Select-Object -First 1;" ^
    "$probe=Get-ChildItem '%VENDOR%\ffmpeg-tmp' -Recurse -Filter ffprobe.exe | Select-Object -First 1;" ^
    "New-Item -ItemType Directory -Force '%VENDOR%\ffmpeg' | Out-Null;" ^
    "Copy-Item $bin.FullName '%VENDOR%\ffmpeg\ffmpeg.exe' -Force;" ^
    "Copy-Item $probe.FullName '%VENDOR%\ffmpeg\ffprobe.exe' -Force;" ^
    "Remove-Item -Recurse -Force '%VENDOR%\ffmpeg-tmp','%VENDOR%\ffmpeg.zip'"
  if errorlevel 1 (
    echo ffmpeg download FAILED. Get ffmpeg.exe + ffprobe.exe into %VENDOR%\ffmpeg\ manually.
    pause
    exit /b 1
  )
)

echo Building GUI exe (onedir) ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed --name meetingnotes ^
  --icon "assets\logo.ico" ^
  --distpath dist ^
  --workpath build ^
  --add-data "meetingnotes\webapp\templates;meetingnotes\webapp\templates" ^
  --add-data "meetingnotes\webapp\static;meetingnotes\webapp\static" ^
  --add-data "meetingnotes\profiles;meetingnotes\profiles" ^
  --add-data "meetingnotes\native;meetingnotes\native" ^
  --add-data "assets;assets" ^
  --add-data "%VENDOR%\ffmpeg;ffmpeg" ^
  --collect-data faster_whisper ^
  --collect-binaries ctranslate2 ^
  --collect-all nvidia.cublas ^
  --collect-all nvidia.cuda_nvrtc ^
  --collect-data certifi ^
  --hidden-import comtypes --collect-all comtypes ^
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
  --add-data "%VENDOR%\ffmpeg;ffmpeg" ^
  --collect-data faster_whisper ^
  --collect-binaries ctranslate2 ^
  --collect-all nvidia.cublas ^
  --collect-all nvidia.cuda_nvrtc ^
  --collect-data certifi ^
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
powershell -NoProfile -Command "Compress-Archive -Path 'dist\%FOLDER%\*' -DestinationPath 'dist\MeetingNotes-%VERSION%-win64.zip' -Force"

echo Post-build smoke gate ...
".venv\Scripts\python.exe" "scripts\smoke_release.py" "dist\MeetingNotes-%VERSION%-win64.zip"
if errorlevel 1 (
  echo SMOKE GATE FAILED - do not ship this build.
  pause
  exit /b 1
)

echo.
echo Done: dist\%FOLDER%\ (run meetingnotes.exe) + dist\MeetingNotes-%VERSION%-win64.zip
endlocal
