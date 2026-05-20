"""Localiza binários externos (ffmpeg, mkvmerge, mkvextract).

Ordem de resolução:
1. Empacotado com o app (PyInstaller `_MEIPASS`)
2. Ao lado do executável (ou em `bin/` ao lado) — modo distribuição:
   basta a galera deixar ffmpeg.exe/mkvmerge.exe junto do Ancopy.exe
3. Encontrado no PATH do sistema
4. Pasta configurada pelo usuário em settings (`binaries_dir`)

Se nenhuma das opções achar, levanta `BinaryNotFound`.
"""
import os
import shutil
import sys

from utils.paths import application_path, resource_path

_BIN_EXT = ".exe" if sys.platform == "win32" else ""


class BinaryNotFound(Exception):
    pass


def find_binary(name: str, extra_dir: str = "") -> str:
    """Devolve o caminho absoluto do binário `name` (sem extensão).

    `extra_dir` é a pasta opcional configurada em settings.
    """
    exe = name + _BIN_EXT
    app_dir = application_path()

    # 1. Embutido (_MEIPASS) + 2. ao lado do executável (ou bin/ ao lado).
    # application_path() retorna a pasta do .exe quando frozen — permite
    # distribuir o app com os binários ao lado, sem instalar nada.
    for candidate in (
        resource_path(exe),
        resource_path(os.path.join("bin", exe)),
        os.path.join(app_dir, exe),
        os.path.join(app_dir, "bin", exe),
    ):
        if os.path.isfile(candidate):
            return candidate

    # 3. PATH
    found = shutil.which(name) or shutil.which(exe)
    if found:
        return found

    # 4. Pasta extra configurada
    if extra_dir:
        candidate = os.path.join(extra_dir, exe)
        if os.path.isfile(candidate):
            return candidate

    raise BinaryNotFound(
        f"Binário '{name}' não encontrado. Coloque {exe} na pasta do app, "
        "instale o MKVToolNix (https://mkvtoolnix.download/), ou configure "
        "a pasta dos binários em ⚙️."
    )
