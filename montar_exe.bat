@echo off
cd /d "%~dp0"
if not exist modelos mkdir modelos
if not exist produtos mkdir produtos
if not exist recursos mkdir recursos
py -3 -m pip install -r requirements.txt pyinstaller
rem As opcoes do build (dados, hidden imports, tkinterdnd2, sem console) ficam em Album.spec.
py -3 -m PyInstaller --noconfirm --clean Album.spec
if errorlevel 1 (
  echo.
  echo Falha no PyInstaller.
  pause
  exit /b 1
)
py -3 -B gerar_lancador.py
echo.
echo Pasta pronta: dist\Album\Album.exe
echo Arraste pastas, fotos ou projetos .album.json em cima de SmartAlbum.exe ou dist\Album\Album.exe para abrir.
pause
