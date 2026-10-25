@echo off
rem Construit "AGOA DATA.exe" (test local ; la publication se fait par GitHub Actions)
py -m pip install --upgrade pip
py -m pip install -r requirements.txt pyinstaller
py -m unittest discover -s tests
if errorlevel 1 goto fin
py -m PyInstaller --noconsole --onefile --icon icon.ico --add-data "icon.ico;." --collect-submodules laspy --hidden-import lazrs --collect-data pyproj --name "AGOA DATA" agoa_app.py
:fin
pause
