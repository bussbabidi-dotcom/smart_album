@echo off
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt pyinstaller
py -3 -m PyInstaller --noconfirm --clean --onedir --windowed --name Album ^
  --add-data "produtos;produtos" ^
  --add-data "modelos;modelos" ^
  --add-data "recursos;recursos" ^
  --add-data "lightroom_plugin;lightroom_plugin" ^
  --collect-all tkinterdnd2 ^
  --hidden-import win32timezone ^
  app.pyw
echo.
echo Pasta pronta para copiar: dist\Album
echo Arraste uma pasta de fotos em cima de Album.exe para abrir com ela.
pause
