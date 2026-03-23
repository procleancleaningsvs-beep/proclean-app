@echo off
cd /d %~dp0
if exist "C:\Program Files\LibreOffice\program\soffice.exe" set "PROCLEAN_LIBREOFFICE=C:\Program Files\LibreOffice\program\soffice.exe"
if exist "C:\Program Files (x86)\LibreOffice\program\soffice.exe" set "PROCLEAN_LIBREOFFICE=C:\Program Files (x86)\LibreOffice\program\soffice.exe"
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
pause
