# PyInstaller spec — versão DEBUG do AniRecap.
#
# Diferenças do AniRecap.spec normal:
# - console=True → janela preta com stack trace de erros
# - upx=False    → sem compressão (elimina UPX como variável de bug)
# - name="AniRecap-debug" → output em dist/AniRecap-debug/AniRecap-debug.exe
#
# Use quando o AniRecap.exe normal não abre — vai aparecer o erro no console.

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

datas = []
datas += collect_data_files("customtkinter")
datas += collect_data_files("tkinterdnd2")
if os.path.isfile("AniRecap_icon.ico"):
    datas += [("AniRecap_icon.ico", ".")]

hiddenimports = []
hiddenimports += collect_submodules("customtkinter")
hiddenimports += collect_submodules("tkinterdnd2")
hiddenimports += ["cv2"]

try:
    import faster_whisper  # noqa
    hiddenimports += collect_submodules("faster_whisper")
    hiddenimports += collect_submodules("ctranslate2")
    hiddenimports += collect_submodules("tokenizers")
except ImportError:
    pass

hiddenimports += [
    "core.ad_transcribe", "core.anilist", "core.audio_post",
    "core.audio_signal", "core.beat_archetypes", "core.cache",
    "core.captions", "core.chunking", "core.cue", "core.face_detect",
    "core.matcher", "core.metadata", "core.mkv", "core.music",
    "core.name_mapper", "core.scene_detect", "core.script",
    "core.subtitle", "core.translator", "core.tts", "core.video",
    "core.visual_index",
    "providers.navy",
    "ui.app", "ui.metadata_modal", "ui.music_picker",
    "ui.settings_modal", "ui.style", "ui.track_selector",
    "ui.update_modal", "ui.voice_picker",
    "utils.binaries", "utils.paths",
]

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
        "torch", "torchvision", "torchaudio",
        "matplotlib", "scipy", "numpy.testing",
        "tkinter.test", "test", "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AniRecap-debug",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # <<< SEM compressão pra evitar bug de UPX
    console=True,       # <<< COM console pra ver stack trace
    disable_windowed_traceback=False,
    icon="AniRecap_icon.ico" if os.path.isfile("AniRecap_icon.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AniRecap-debug",
)
