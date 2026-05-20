# PyInstaller spec — gera o .exe do Ancopy.
#
# Estratégia "one-folder" (mais rápido pra carregar que one-file).
# Saída: dist/Ancopy/Ancopy.exe (pasta com .exe + libs ao lado).
#
# Decisões de empacotamento:
# - ffmpeg / mkvmerge / mkvextract: NÃO incluídos (cada ~80MB). User
#   configura `binaries_dir` nos settings ou tem no PATH.
# - faster-whisper models: NÃO incluídos (modelos ~500MB cada, baixam
#   on-demand do HuggingFace na primeira execução do AD).
# - lbpcascade_animeface.xml: NÃO incluído (baixa on-demand).
# - customtkinter + tkinterdnd2 assets: incluídos via collect_data.
#
# Pra rodar: `pyinstaller Ancopy.spec` (depois de `pip install pyinstaller`).
# Ou simplesmente clica em `build.bat`.

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# ─── Data files (assets que ficam dentro do bundle) ──────────────────
datas = []

# customtkinter: ícones, fontes, temas
datas += collect_data_files("customtkinter")

# tkinterdnd2: DLLs/.tcl do drag-and-drop
datas += collect_data_files("tkinterdnd2")

# Ícone do app (raiz do projeto, opcional — existe se user adicionou)
if os.path.isfile("Ancopy_icon.ico"):
    datas += [("Ancopy_icon.ico", ".")]

# ─── Hidden imports (módulos que PyInstaller não detecta auto) ───────
hiddenimports = []
hiddenimports += collect_submodules("customtkinter")
hiddenimports += collect_submodules("tkinterdnd2")

# opencv-python: usa namespace cv2 mas tem submódulos dinâmicos
hiddenimports += [
    "cv2",
]

# faster-whisper depende de ctranslate2 e tokenizers — se instalado
try:
    import faster_whisper  # noqa
    hiddenimports += collect_submodules("faster_whisper")
    hiddenimports += collect_submodules("ctranslate2")
    hiddenimports += collect_submodules("tokenizers")
except ImportError:
    pass

# Módulos do próprio app (importação dinâmica em runtime)
hiddenimports += [
    "core.ad_transcribe",
    "core.anilist",
    "core.audio_post",
    "core.audio_signal",
    "core.beat_archetypes",
    "core.cache",
    "core.captions",
    "core.chunking",
    "core.cue",
    "core.face_detect",
    "core.matcher",
    "core.metadata",
    "core.mkv",
    "core.music",
    "core.name_mapper",
    "core.scene_detect",
    "core.script",
    "core.subtitle",
    "core.translator",
    "core.tts",
    "core.video",
    "core.visual_index",
    "providers.navy",
    "ui.app",
    "ui.metadata_modal",
    "ui.music_picker",
    "ui.settings_modal",
    "ui.style",
    "ui.track_selector",
    "ui.update_modal",
    "ui.voice_picker",
    "utils.binaries",
    "utils.paths",
]


# ─── Análise principal ───────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Reduz tamanho excluindo módulos pesados não usados.
        # torch/torchvision/torchaudio: ~4GB! Eram puxados só por um
        # `import torch` opcional em _pick_device (já trocado por
        # ctranslate2). O app NÃO precisa de PyTorch.
        "torch",
        "torchvision",
        "torchaudio",
        "matplotlib",
        "scipy",
        "numpy.testing",
        "PIL.ImageTk",
        "tkinter.test",
        "test",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ─── EXE ─────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ancopy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # comprime (precisa de UPX instalado; falha silenciosa se não tem)
    upx_exclude=[
        # Compressão UPX em DLLs do Tk às vezes quebra
        "tcl*.dll",
        "tk*.dll",
        "vcruntime*.dll",
    ],
    console=False,      # windowed mode (sem console preto)
    disable_windowed_traceback=False,
    icon="Ancopy_icon.ico" if os.path.isfile("Ancopy_icon.ico") else None,
)

# ─── COLLECT (gera a pasta dist/Ancopy/ com tudo dentro) ─────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Ancopy",
)
