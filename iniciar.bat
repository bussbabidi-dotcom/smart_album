@echo off
rem Arraste uma pasta de fotos em cima deste arquivo para abrir o programa com ela.
cd /d "%~dp0"
where pyw >nul 2>&1 && start "" pyw -3 "%~dp0app.pyw" %* && exit /b 0
start "" pythonw "%~dp0app.pyw" %*
