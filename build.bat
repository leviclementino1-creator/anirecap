@echo off
REM ─── Build do Ancopy.exe ──────────────────────────────────────────
REM Roda pyinstaller com o Ancopy.spec. Output em dist\Ancopy\Ancopy.exe.
REM
REM Pré-requisitos:
REM   - Python 3.11+ no PATH
REM   - pip install -r requirements.txt
REM   - pip install pyinstaller
REM
REM Opcional pra reduzir tamanho do .exe:
REM   - UPX instalado e no PATH (https://github.com/upx/upx/releases)
REM ──────────────────────────────────────────────────────────────────

setlocal

echo.
echo === Ancopy build ===
echo.

REM 1. Verifica python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    echo Instale Python 3.11+ e marque "Add to PATH" no instalador.
    exit /b 1
)

REM 2. Verifica pyinstaller
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller nao instalado. Instalando agora...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERRO] Falha ao instalar PyInstaller.
        exit /b 1
    )
)

REM 3. Verifica deps do projeto
echo Verificando dependencias do app...
python -c "import customtkinter, tkinterdnd2, requests, cv2" 2>nul
if errorlevel 1 (
    echo Algumas deps faltando. Instalando requirements.txt...
    python -m pip install -r requirements.txt
)

REM 4. Limpa builds anteriores
if exist build (
    echo Limpando build/ anterior...
    rmdir /s /q build
)
if exist dist (
    echo Limpando dist/ anterior...
    rmdir /s /q dist
)

REM 5. Roda PyInstaller
echo.
echo Buildando... (pode demorar 1-3 minutos)
python -m PyInstaller --clean --noconfirm Ancopy.spec
if errorlevel 1 (
    echo.
    echo [ERRO] Build falhou. Veja mensagens acima.
    exit /b 1
)

REM 6. Resultado
echo.
echo ============================================
echo Build OK!
echo Executavel: dist\Ancopy\Ancopy.exe
echo ============================================
echo.
echo IMPORTANTE — antes de distribuir:
echo  1. Copie ffmpeg.exe, mkvmerge.exe e mkvextract.exe pra dist\Ancopy\
echo     (ou configure binaries_dir nas configuracoes do app).
echo  2. Primeira execucao do AD baixa modelo Whisper (~500MB).
echo  3. config.json eh criado ao lado do .exe.
echo.

endlocal
