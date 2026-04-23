"""Localiza binários externos (ffmpeg, mkvmerge, mkvextract).

Ordem de resolução:
1. Empacotado com o app (PyInstaller `_MEIPASS` ou pasta raiz no modo dev)
2. Encontrado no PATH do sistema
3. Pasta configurada pelo usuário em settings (`binaries_dir`)

Se nenhuma das três opções achar, levanta `BinaryNotFound`.
"""
import os
import shutil
import sys

from utils.paths import resource_path

_BIN_EXT = ".exe" if sys.platform == "win32" else ""


class BinaryNotFound(Exception):
    pass


def find_binary(name: str, extra_dir: str = "") -> str:
    """Devolve o caminho absoluto do binário `name` (sem extensão).

    `extra_dir` é a pasta opcional configurada em settings.
    """
    exe = name + _BIN_EXT

    # 1. Embutido na raiz do app ou em bin/
    for candidate in (resource_path(exe), resource_path(os.path.join("bin", exe))):
        if os.path.isfile(candidate):
            return candidate

    # 2. PATH
    found = shutil.which(name) or shutil.which(exe)
    if found:
        return found

    # 3. Pasta extra configurada
    if extra_dir:
        candidate = os.path.join(extra_dir, exe)
        if os.path.isfile(candidate):
            return candidate

    raise BinaryNotFound(
        f"Binário '{name}' não encontrado. Instale o MKVToolNix "
        "(https://mkvtoolnix.download/) ou configure a pasta dos binários em ⚙️."
    )
