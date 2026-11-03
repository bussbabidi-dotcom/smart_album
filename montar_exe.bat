@echo off
cd /d "%~dp0"
if not exist modelos mkdir modelos
if not exist produtos mkdir produtos
if not exist recursos mkdir recursos
py -3 -m pip install -r requirements.txt pyinstaller
py -3 -m PyInstaller --noconfirm --clean --onedir --windowed --name Album ^
  --add-data "produtos;produtos" ^
  --add-data "modelos;modelos" ^
  --add-data "recursos;recursos" ^
  --add-data "lightroom_plugin;lightroom_plugin" ^
  --collect-all tkinterdnd2 ^
  --hidden-import win32timezone ^
  --hidden-import janela_tratamento ^
  --hidden-import editor_foto ^
  --hidden-import exportar_psd ^
  app.pyw
echo.
echo Pasta pronta: dist\Album\Album.exe
echo Arraste pastas, fotos ou projetos .album.json em cima de SmartAlbum.exe ou dist\Album\Album.exe para abrir.
pause
