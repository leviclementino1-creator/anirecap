import os
import sys


def application_path() -> str:
    """Pasta raiz do app — onde config.json fica ao lado do executável."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(relative_path: str) -> str:
    """Caminho para recursos empacotados (ícones, binários vendorizados).

    No modo PyInstaller lê de sys._MEIPASS; em desenvolvimento cai na raiz do projeto.
    """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = application_path()
    return os.path.join(base_path, relative_path)
