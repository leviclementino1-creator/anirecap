@echo off
REM ─── Compila o instalador AniRecap-Setup-X.Y.Z.exe ────────────────
REM Pré-requisito: build.bat já rodado (dist\AniRecap\ montada com
REM binários + config.json sanitizado) e Inno Setup 6 instalado.
REM
REM Lê VERSAO_ATUAL do config.py e passa pro .iss automaticamente.
REM Output: dist\AniRecap-Setup-X.Y.Z.exe
REM ──────────────────────────────────────────────────────────────────

setlocal

for /f "delims=" %%v in ('python -c "import config; print(config.VERSAO_ATUAL)"') do set VERSAO=%%v
if "%VERSAO%"=="" (
    echo [ERRO] Nao consegui ler VERSAO_ATUAL do config.py
    exit /b 1
)

set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    echo [ERRO] Inno Setup 6 nao encontrado. Instale com:
    echo   winget install -e --id JRSoftware.InnoSetup
    exit /b 1
)

echo Compilando instalador v%VERSAO%...
%ISCC% /DAppVersion=%VERSAO% AniRecap.iss
if errorlevel 1 (
    echo [ERRO] Compilacao do instalador falhou.
    exit /b 1
)

echo.
echo ============================================
echo Instalador OK: dist\AniRecap-Setup-%VERSAO%.exe
echo ============================================

endlocal
