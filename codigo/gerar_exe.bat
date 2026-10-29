@echo off
chcp 65001 >nul
title Gerando Peninha.exe
cd /d "%~dp0"

echo.
echo  === Gerando o Peninha.exe ===
echo.

rem ---------- 1. Encontrar (ou instalar) o Python ----------
set "PY="
where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not defined PY (
    echo  [0/2] Python nao encontrado. Baixando e instalando automaticamente...
    echo        ^(so acontece uma vez, leva 1 ou 2 minutos^)
    curl -L -s -o "%TEMP%\python-instalador.exe" https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
    if errorlevel 1 (
        echo  Nao consegui baixar o Python. Verifique a internet e tente de novo.
        pause
        exit /b 1
    )
    "%TEMP%\python-instalador.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_tcltk=1 Include_pip=1 Include_launcher=0
    del /q "%TEMP%\python-instalador.exe" >nul 2>nul
    set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

if not exist "%PY%" if not "%PY%"=="python" (
    echo  A instalacao do Python nao terminou. Rode este arquivo de novo.
    pause
    exit /b 1
)

rem ---------- 2. Dependencias ----------
echo  [1/2] Instalando o que o programa precisa...
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet paho-mqtt cryptography pyinstaller
if errorlevel 1 (
    echo  Erro ao instalar as dependencias. Verifique a internet e tente de novo.
    pause
    exit /b 1
)

rem ---------- 3. Gerar o executavel ----------
echo  [2/2] Montando o executavel ^(pode levar 1 ou 2 minutos^)...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name Peninha --icon "imagens\peninha.ico" --add-data "imagens;imagens" peninha.py >build.log 2>&1
if errorlevel 1 (
    echo  Algo deu errado. Os detalhes estao em build.log
    pause
    exit /b 1
)

copy /y "dist\Peninha.exe" "..\Peninha.exe" >nul
rmdir /s /q build >nul 2>nul
rmdir /s /q dist >nul 2>nul
del /q Peninha.spec >nul 2>nul
del /q build.log >nul 2>nul

echo.
echo  Pronto! O arquivo Peninha.exe esta na pasta de cima ^(fora de "codigo"^).
echo  Copie ele para o outro computador e abra nos dois.
echo  ^(O outro computador NAO precisa de Python: ele ja vai dentro do .exe^)
echo.
pause
