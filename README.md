# AniRecap

Transforma um episódio `.mkv` de anime em um **short vertical 9:16** com
narração TTS em PT-BR, legendas queimadas palavra-por-palavra, cortes
sincronizados com a narração e trilha de fundo — pronto pra postar em
TikTok / Reels / YouTube Shorts.

## Como funciona

1. **Arrasta o `.mkv`** — o app extrai a legenda (e Audio Description, se
   houver) via mkvtoolnix
2. **✨ Resumo** — LLM resume o episódio (300-500 palavras)
3. **📝 Short** — LLM condensa em roteiro de ~180 palavras com hook
4. **🎙️ Narração** — ElevenLabs gera a voz com timestamps por caractere
5. **🎬 Plano** — a narração vira beats de 2-3s; um matcher LLM casa cada
   beat com a cena certa do episódio (diálogo + audio description +
   envelope de áudio + arquétipos narrativos)
6. **Editor visual** — timeline com thumbnail de cada beat; troca qualquer
   cena com 2 cliques antes de renderizar
7. **Render** — ffmpeg monta o 9:16 com blur de fundo, queima as legendas
   e mixa narração + música

## Requisitos

- Windows 10/11
- API key da [Navy AI](https://api.navy) (Gemini) — roteiro e matcher
- API key do [ElevenLabs](https://elevenlabs.io) — narração
- ffmpeg + mkvtoolnix (já inclusos no zip da release)

## Instalação

Baixa o `AniRecap-Setup-X.Y.Z.exe` da [release mais recente](../../releases/latest)
e executa — instala tudo (ffmpeg e mkvtoolnix inclusos), cria atalho e
pronto. Não precisa de admin. Na primeira execução, configura as API keys
em ⚙️.

O app se atualiza sozinho quando sai versão nova (avisa na abertura; dá
pra checar manualmente no botão `vX.Y.Z` do topo). Configurações e músicas
são preservadas em updates.

Trilhas de fundo: coloca seus `.mp3` na pasta `music/` da instalação
(`%LOCALAPPDATA%\AniRecap\music`).

Prefere portátil? A release também tem o `AniRecap.zip` — extrai e usa.

## Build a partir do código

Veja [BUILD.md](BUILD.md). Resumo: Python 3.11+, `pip install -r
requirements.txt`, `build.bat`.
