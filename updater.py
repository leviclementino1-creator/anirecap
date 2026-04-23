"""Verificação de versão e auto-update (igual ao app 1.2.4, só isolado em módulo)."""
import os
import subprocess
import sys
import threading

import requests

from config import URL_VERSAO, VERSAO_ATUAL


def check_async(on_update_available):
    """Consulta o endpoint de versão em thread daemon.

    Chama `on_update_available(link_download, nova_versao)` se existir atualização.
    Silencia erros de rede (sem popup).
    """
    def _run():
        try:
            resposta = requests.get(URL_VERSAO, timeout=5)
            dados = resposta.json()
            if dados.get("versao") and dados["versao"] != VERSAO_ATUAL:
                on_update_available(dados["link"], dados["versao"])
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def baixar_e_reiniciar(link_download: str, on_error):
    """Baixa o .exe novo, grava um .bat que troca o binário e reinicia o app.

    Usa o mesmo truque do 'explorer.exe' do app 1.2.4 para cortar herança de
    variáveis de ambiente e simular um clique manual.
    """
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
        app_path = sys.executable
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))
        app_path = os.path.abspath(__file__)

    nome_atual = os.path.basename(app_path)
    caminho_novo = os.path.join(app_dir, "update_temp.exe")
    caminho_bat = os.path.join(app_dir, "atualizar.bat")

    try:
        resposta = requests.get(link_download, stream=True)
        with open(caminho_novo, 'wb') as f:
            for chunk in resposta.iter_content(chunk_size=8192):
                f.write(chunk)

        script_bat = f"""@echo off
timeout /t 2 /nobreak > NUL
cd /d "{app_dir}"
:RETRY
del "{nome_atual}"
if exist "{nome_atual}" (
    timeout /t 1 /nobreak > NUL
    goto RETRY
)
ren "update_temp.exe" "{nome_atual}"
explorer.exe "%CD%\\{nome_atual}"
del "%~f0"
"""
        with open(caminho_bat, "w", encoding="utf-8") as f:
            f.write(script_bat)

        subprocess.Popen(caminho_bat, shell=True, creationflags=0x00000008)
        os._exit(0)
    except Exception as e:
        on_error(str(e))
