@echo off
rem Arraste uma pasta de fotos, fotos avulsas ou um arquivo .album.json em cima deste arquivo para abrir o programa.
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"
if exist "%~dp0dist\Album\Album.exe" (
  start "" "%~dp0dist\Album\Album.exe" %*
  exit /b 0
)
where pyw >nul 2>&1 && start "" pyw -3 -B "%~dp0app.pyw" %* && exit /b 0
start "" pythonw -B "%~dp0app.pyw" %*

