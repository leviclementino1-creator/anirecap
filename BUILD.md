# Build do Ancopy.exe

Gera o executável `dist/Ancopy/Ancopy.exe` via PyInstaller.

## Pré-requisitos

- **Python 3.11+** no PATH
- **ffmpeg.exe**, **mkvmerge.exe**, **mkvextract.exe** (binários externos — não vão dentro do .exe, ficam ao lado)

## Build rápido

```bat
build.bat
```

Output: `dist\Ancopy\Ancopy.exe` (pasta inteira com o exe + libs ao lado).

O script faz tudo:
1. Verifica Python e PyInstaller (instala se faltar)
2. Verifica deps do `requirements.txt`
3. Limpa `build/` e `dist/` anteriores
4. Roda `pyinstaller Ancopy.spec`

Demora ~1-3 minutos.

## Build manual

```bat
pip install pyinstaller
pip install -r requirements.txt
pyinstaller --clean --noconfirm Ancopy.spec
```

## Depois de buildar

O `.exe` precisa de binários externos pra extrair legendas e cortar vídeo:

### Opção 1 — Binários junto do .exe (portátil)

Copie pra `dist\Ancopy\`:
- `ffmpeg.exe` ([download](https://www.gyan.dev/ffmpeg/builds/))
- `mkvmerge.exe` e `mkvextract.exe` ([MKVToolNix](https://mkvtoolnix.download/))

O app procura nesta ordem:
1. Ao lado do .exe
2. Em `bin/` subpasta
3. Pasta configurada em `binaries_dir` nas settings
4. PATH do sistema

### Opção 2 — Já instalados no PATH

Se já tem MKVToolNix e ffmpeg instalados, o app acha sozinho.

### Opção 3 — Pasta dedicada (settings)

Abre o `.exe`, vai em ⚙️ → **"Pasta dos binários"** e aponta pra onde os binários estão.

## Caveats

- **Primeira execução do AD**: baixa modelo Whisper `medium` (~500MB) do HuggingFace. Cache em `%TEMP%\ancopy\cache\`.
- **`lbpcascade_animeface.xml`**: baixado on-demand do GitHub.
- **config.json**: salvo ao lado do `Ancopy.exe` na primeira execução.
- **Antivírus** às vezes flagga PyInstaller. Se acusar falso positivo, adiciona o `.exe` na exceção do Defender/AV.

## Reduzir tamanho do .exe

- **UPX** comprime binaries em até 50%. Já configurado no spec; só precisa de UPX instalado e no PATH ([download](https://github.com/upx/upx/releases)).
- **Excluir faster-whisper** se não usar AD: comenta `import faster_whisper` em `core/ad_transcribe.py` antes de buildar.

## Distribuição

Pra mandar pra outra pessoa, zipa a pasta `dist\Ancopy\` inteira. Tudo o que precisa pra rodar tá nela.

Tamanho típico:
- Sem UPX, sem AD: ~150MB
- Com UPX, sem AD: ~80MB
- Com AD (faster-whisper + ctranslate2): +~200MB

## Troubleshooting

**"DLL load failed"** — geralmente é Visual C++ Redistributable faltando. Instala o [VC++ 2015-2022 Redistributable x64](https://aka.ms/vs/17/release/vc_redist.x64.exe).

**"customtkinter assets missing"** — `pyinstaller --clean --noconfirm Ancopy.spec` (o `--clean` força recompilar com data files atualizados).

**"tkinterdnd2 não funciona"** — em casos raros, copia manualmente `tkdnd/` da `tkinterdnd2` pra `dist\Ancopy\_internal\tkinterdnd2\`.

**Build muito lento** — primeira vez sempre é mais lenta (PyInstaller analisa imports). Builds subsequentes usam cache em `build/`.
