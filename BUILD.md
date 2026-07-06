# Build do AniRecap.exe

Gera o executável `dist/AniRecap/AniRecap.exe` via PyInstaller.

## Pré-requisitos

- **Python 3.11+** no PATH
- **ffmpeg.exe**, **mkvmerge.exe**, **mkvextract.exe** (binários externos — não vão dentro do .exe, ficam ao lado)

## Build rápido

```bat
build.bat
```

Output: `dist\AniRecap\AniRecap.exe` (pasta inteira com o exe + libs ao lado).

O script faz tudo:
1. Verifica Python e PyInstaller (instala se faltar)
2. Verifica deps do `requirements.txt`
3. Limpa `build/` e `dist/` anteriores
4. Roda `pyinstaller AniRecap.spec`

Demora ~1-3 minutos.

## Build manual

```bat
pip install pyinstaller
pip install -r requirements.txt
pyinstaller --clean --noconfirm AniRecap.spec
```

## Depois de buildar

O `.exe` precisa de binários externos pra extrair legendas e cortar vídeo:

### Opção 1 — Binários junto do .exe (portátil)

Copie pra `dist\AniRecap\`:
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
- **config.json**: salvo ao lado do `AniRecap.exe` na primeira execução.
- **Antivírus** às vezes flagga PyInstaller. Se acusar falso positivo, adiciona o `.exe` na exceção do Defender/AV.

## Reduzir tamanho do .exe

- **UPX** comprime binaries em até 50%. Já configurado no spec; só precisa de UPX instalado e no PATH ([download](https://github.com/upx/upx/releases)).
- **Excluir faster-whisper** se não usar AD: comenta `import faster_whisper` em `core/ad_transcribe.py` antes de buildar.

## Distribuição

Pra mandar pra outra pessoa, zipa a pasta `dist\AniRecap\` inteira. Tudo o que precisa pra rodar tá nela.

**IMPORTANTE**: antes de zipar, troca o `config.json` da pasta por um sanitizado (sem API keys). O auto-update preserva o `config.json` de quem recebe, então cada pessoa configura as próprias keys uma vez só.

## Instalador (AniRecap-Setup-X.Y.Z.exe)

Um único .exe que instala tudo (app + ffmpeg + mkvtoolnix + atalhos + desinstalador), sem pedir admin — vai pra `%LOCALAPPDATA%\AniRecap`.

Pré-requisito (uma vez): `winget install -e --id JRSoftware.InnoSetup`

```bat
build.bat            REM gera dist\AniRecap\
REM ... copia binários + config sanitizado pra dist\AniRecap\ ...
build-installer.bat  REM gera dist\AniRecap-Setup-X.Y.Z.exe
```

O instalador **preserva** `config.json` e `music/` em updates e desinstalação.

## Publicar release (auto-update)

O app checa `github.com/<GITHUB_REPO>/releases/latest` na abertura (repo configurado em `config.py`) e também no botão `vX.Y.Z` do topo da janela. Pra publicar uma versão nova:

1. Bumpa `VERSAO_ATUAL` em `config.py` (ex: `"2.1.0"`)
2. `build.bat` → monta `dist\AniRecap\` (binários + config sanitizado!)
3. `build-installer.bat` → `dist\AniRecap-Setup-2.1.0.exe`
4. (Opcional) zipa `dist\AniRecap\` como `AniRecap.zip` (versão portátil)
5. Cria a release com tag `v2.1.0` e anexa:
   ```bat
   gh release create v2.1.0 dist\AniRecap-Setup-2.1.0.exe dist\AniRecap.zip --title "AniRecap 2.1.0" --notes "changelog aqui"
   ```

Quem tiver versão antiga recebe o popup na próxima abertura. O updater baixa o **Setup.exe** (preferido; roda silencioso e reabre o app) ou o zip (fallback portátil). Nos dois caminhos, `config.json` e `music/` do usuário são **preservados**.

Tamanho típico:
- Sem UPX, sem AD: ~150MB
- Com UPX, sem AD: ~80MB
- Com AD (faster-whisper + ctranslate2): +~200MB

## Troubleshooting

**"DLL load failed"** — geralmente é Visual C++ Redistributable faltando. Instala o [VC++ 2015-2022 Redistributable x64](https://aka.ms/vs/17/release/vc_redist.x64.exe).

**"customtkinter assets missing"** — `pyinstaller --clean --noconfirm AniRecap.spec` (o `--clean` força recompilar com data files atualizados).

**"tkinterdnd2 não funciona"** — em casos raros, copia manualmente `tkdnd/` da `tkinterdnd2` pra `dist\AniRecap\_internal\tkinterdnd2\`.

**Build muito lento** — primeira vez sempre é mais lenta (PyInstaller analisa imports). Builds subsequentes usam cache em `build/`.
