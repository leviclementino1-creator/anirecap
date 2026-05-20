@echo off
REM ─── Build do Ancopy-debug.exe ────────────────────────────────────
REM Versao com console aberto pra ver erros quando o exe nao abre.
REM Output: dist\Ancopy-debug\Ancopy-debug.exe
REM
REM Diferenças do build.bat normal:
REM - Console habilitado (janela preta com stack trace)
REM - Sem UPX (elimina bug de compressao)
REM ──────────────────────────────────────────────────────────────────

setlocal

echo.
echo === Ancopy-debug build ===
echo.

REM 1. PyInstaller
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    python -m pip install pyinstaller
)

REM 2. Limpa builds anteriores do debug
if exist build\Ancopy-debug rmdir /s /q build\Ancopy-debug
if exist dist\Ancopy-debug rmdir /s /q dist\Ancopy-debug

REM 3. Build
echo Buildando com console + sem UPX...
python -m PyInstaller --clean --noconfirm Ancopy-debug.spec
if errorlevel 1 (
    echo [ERRO] Build falhou.
    exit /b 1
)

REM 4. Copia config.json se existir
if exist config.json (
    copy /Y config.json dist\Ancopy-debug\ >nul
    echo config.json copiado.
)

echo.
echo ============================================
echo Build OK!
echo Executavel: dist\Ancopy-debug\Ancopy-debug.exe
echo.
echo Pra testar: clique no exe acima ou rode:
echo   dist\Ancopy-debug\Ancopy-debug.exe
echo.
echo Como esta com console aberto, qualquer erro
echo de import vai aparecer na janela preta.
echo Tira print do erro e me mostra.
echo ============================================
echo.

endlocal
