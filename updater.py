"""Auto-update via GitHub Releases.

Fluxo:
1. `check_async` consulta a API do GitHub (releases/latest). Se a tag é mais
   nova que VERSAO_ATUAL e a release tem um asset .zip, avisa a UI.
2. `baixar_e_reiniciar` baixa o zip, extrai pra `update_tmp/` ao lado do exe
   e grava um .bat que: espera o app fechar, copia os arquivos novos por
   cima (robocopy /E), e reabre o app.

O update PRESERVA os dados do usuário:
- `config.json` é excluído da cópia (/XF) — as API keys dele ficam intactas.
- `music/` e outros arquivos locais não são apagados (sem /MIR, nada é
  removido — só sobrescrito/adicionado).

Só funciona no modo frozen (.exe). Em dev (python main.py) recusa com erro
amigável — ninguém quer um robocopy por cima do repositório.
"""
import os
import subprocess
import sys
import threading
import zipfile

import requests

from config import GITHUB_REPO, VERSAO_ATUAL

_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_ver(v: str) -> tuple:
    """'v2.1.0' → (2, 1, 0). Tolera sufixos ('2.1.0-beta' → (2, 1, 0))."""
    v = (v or "").strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def _is_newer(remote: str, local: str) -> bool:
    return _parse_ver(remote) > _parse_ver(local)


def _pick_asset(assets: list) -> str:
    """Escolhe o melhor asset da release: instalador Setup.exe > zip.

    O instalador atualiza tudo (arquivos, atalhos) e preserva config/music
    via regras do Inno Setup. O zip é o fallback pra instalações portáteis
    antigas ou releases sem instalador.
    """
    setup = next(
        (
            a.get("browser_download_url")
            for a in assets
            if str(a.get("name", "")).lower().endswith(".exe")
            and "setup" in str(a.get("name", "")).lower()
        ),
        "",
    )
    if setup:
        return setup
    return next(
        (
            a.get("browser_download_url")
            for a in assets
            if str(a.get("name", "")).lower().endswith(".zip")
        ),
        "",
    )


def check_async(on_update_available, on_no_update=None, on_error=None):
    """Consulta a release mais recente no GitHub em thread daemon.

    - `on_update_available(url, nova_versao)`: existe versão mais nova.
    - `on_no_update()`: opcional — já está na mais recente (usado pelo
      botão manual de "verificar atualização"; o check automático da
      abertura não passa esse callback e fica silencioso).
    - `on_error(msg)`: opcional — falha de rede/API (idem, só manual).
    """
    def _run():
        try:
            r = requests.get(
                _API_LATEST, timeout=8,
                headers={"Accept": "application/vnd.github+json"},
            )
            data = r.json()
            tag = data.get("tag_name") or ""
            url = _pick_asset(data.get("assets") or [])
            if tag and url and _is_newer(tag, VERSAO_ATUAL):
                on_update_available(url, tag.lstrip("vV"))
            elif on_no_update:
                on_no_update()
        except Exception as e:
            if on_error:
                on_error(str(e))

    threading.Thread(target=_run, daemon=True).start()


def baixar_e_reiniciar(link_download: str, on_error, on_progress=None):
    """Baixa e aplica a atualização. Dois modos, decididos pela URL:

    - `...Setup.exe` (instalador Inno): baixa pro %TEMP% e roda silencioso
      (/VERYSILENT). O instalador fecha o app, troca os arquivos, preserva
      config.json + music/ (regras do .iss) e reabre o app (/RELAUNCH=1).
    - `.zip` (fallback portátil): extrai e troca a pasta via .bat+robocopy.

    `on_progress(pct)`: callback opcional (0-100) durante o download.
    """
    if not getattr(sys, "frozen", False):
        on_error("Atualização automática só funciona no .exe distribuído.")
        return

    if link_download.lower().endswith(".exe"):
        _update_via_installer(link_download, on_error, on_progress)
        return

    _update_via_zip(link_download, on_error, on_progress)


def _update_via_installer(link_download: str, on_error, on_progress=None):
    import tempfile
    import time
    setup_path = os.path.join(tempfile.gettempdir(), "AniRecap-Setup.exe")
    log_path = os.path.join(tempfile.gettempdir(), "anirecap-update.log")
    try:
        resposta = requests.get(link_download, stream=True, timeout=30)
        resposta.raise_for_status()
        total = int(resposta.headers.get("content-length") or 0)
        done = 0
        with open(setup_path, "wb") as f:
            for chunk in resposta.iter_content(chunk_size=1024 * 256):
                f.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(int(done * 100 / total))

        cmd = [
            setup_path,
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            "/RELAUNCH=1", f"/LOG={log_path}",
        ]

        # O antivírus pode matar/bloquear o instalador recém-baixado na
        # primeira execução (escaneando 200MB). Estratégia: lança, espera
        # alguns segundos e confere se o processo sobreviveu. Se morreu
        # cedo, espera o scan terminar e tenta mais uma vez. Só fecha o
        # app quando o instalador está comprovadamente vivo — assim uma
        # falha vira mensagem de erro em vez de "cliquei e nada aconteceu".
        for tentativa in (1, 2):
            proc = subprocess.Popen(cmd, cwd=tempfile.gettempdir())
            time.sleep(4.0)
            if proc.poll() is None:
                os._exit(0)  # instalador rodando — ele fecha e reabre o app
            if tentativa == 1:
                time.sleep(6.0)  # dá tempo pro scan do antivírus acabar

        on_error(
            "O instalador fechou antes de começar (antivírus?). "
            f"Tente de novo, ou rode manualmente: {setup_path}"
        )
    except Exception as e:
        try:
            os.remove(setup_path)
        except OSError:
            pass
        on_error(str(e))


def _update_via_zip(link_download: str, on_error, on_progress=None):
    app_dir = os.path.dirname(sys.executable)
    exe_name = os.path.basename(sys.executable)
    zip_path = os.path.join(app_dir, "update_download.zip")
    tmp_dir = os.path.join(app_dir, "update_tmp")
    bat_path = os.path.join(app_dir, "atualizar.bat")

    try:
        # --- download com progresso -----------------------------------
        resposta = requests.get(link_download, stream=True, timeout=30)
        resposta.raise_for_status()
        total = int(resposta.headers.get("content-length") or 0)
        done = 0
        with open(zip_path, "wb") as f:
            for chunk in resposta.iter_content(chunk_size=1024 * 256):
                f.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(int(done * 100 / total))

        # --- extração --------------------------------------------------
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_dir)

        # O zip da release contém a pasta raiz "AniRecap/" (mesmo layout do
        # zip de distribuição). Se o zip vier "flat", usa tmp_dir direto.
        entries = os.listdir(tmp_dir)
        if len(entries) == 1 and os.path.isdir(os.path.join(tmp_dir, entries[0])):
            src_rel = os.path.join("update_tmp", entries[0])
        else:
            src_rel = "update_tmp"

        # --- .bat de troca ----------------------------------------------
        # Espera o exe destravar (del falha enquanto o processo vive),
        # copia tudo por cima MENOS config.json (preserva keys do usuário)
        # e reabre via explorer (corta herança de env vars, igual ao 1.2.4).
        script_bat = f"""@echo off
cd /d "{app_dir}"
timeout /t 2 /nobreak > NUL
:RETRY
del "{exe_name}" 2>NUL
if exist "{exe_name}" (
    timeout /t 1 /nobreak > NUL
    goto RETRY
)
robocopy "{src_rel}" "." /E /XF config.json /NFL /NDL /NJH /NJS /NP
rd /s /q "update_tmp"
del "update_download.zip" 2>NUL
explorer.exe "%CD%\\{exe_name}"
del "%~f0"
"""
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(script_bat)

        subprocess.Popen(bat_path, shell=True, creationflags=0x00000008)
        os._exit(0)
    except Exception as e:
        # Limpa artefatos parciais pra não deixar lixo na pasta do app
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        try:
            os.remove(zip_path)
        except OSError:
            pass
        on_error(str(e))
