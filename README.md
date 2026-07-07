<div align="center">

<img src="docs/icon.png" width="110" alt="AniRecap" />

# AniRecap

**Transforma um episódio de anime em um short 9:16 pronto pra postar — com narração, legendas e cortes sincronizados. Tudo automático.**

[![Release](https://img.shields.io/github/v/release/leviclementino1-creator/anirecap?label=vers%C3%A3o&color=8b3ff5)](../../releases/latest)
[![Downloads](https://img.shields.io/github/downloads/leviclementino1-creator/anirecap/total?label=downloads&color=ec4899)](../../releases)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078d4)](#requisitos)

[**⬇️ Baixar a versão mais recente**](../../releases/latest)

</div>

---

## ✨ O que ele faz

Você arrasta um `.mkv` de anime pra janela, clica em 4 botões, e recebe um
`short_final.mp4` vertical (1080x1920) de 60-90s com:

- 🎙️ **Narração em PT-BR** gerada por IA (ElevenLabs), com roteiro no estilo
  shorts/TikTok — hook forte, escalada, gancho no final
- 📝 **Legendas queimadas palavra-por-palavra**, sincronizadas com a voz
- 🎬 **Cortes do episódio casados com a narração**: uma IA lê o episódio e
  escolhe, pra cada frase do roteiro, a cena que mostra aquilo na tela
- 🎵 **Trilha de fundo** (seus .mp3) mixada em volume configurável
- 🖼️ Formato 9:16 com fundo desfocado, pronto pra TikTok / Reels / Shorts

## 🧠 O editor visual de plano

Antes de renderizar, o AniRecap mostra o **plano de cortes**: um card pra
cada trecho da narração, com a **imagem real da cena escolhida**.

- Não gostou de uma cena? **Clica no thumbnail** e escolhe outra — o seletor
  navega o episódio inteiro (◀ ▶ ou digite `mm:ss`)
- Beats onde a IA ficou em dúvida aparecem com **borda vermelha** e trazem
  uma "2ª opção" pré-carregada
- Cada card mostra o arquétipo narrativo (HOOK, ESCALADA, CLIMAX, PAYOFF)
  e o motivo da escolha
- Dá pra **fixar o plano** (override) pra repetir o mesmo resultado, e
  destravar com um clique

## 🚀 Instalação

1. Baixa o **`AniRecap-Setup-X.Y.Z.exe`** da [release mais recente](../../releases/latest)
2. Executa — instala tudo (ffmpeg e mkvtoolnix inclusos), cria atalho no
   desktop, **sem pedir admin**
3. Pronto. O app **se atualiza sozinho** quando sai versão nova (suas
   configurações e músicas são sempre preservadas)

> Prefere sem instalar? Baixa o `AniRecap.zip` (portátil), extrai e abre o
> `AniRecap.exe`.

## 🔑 Configuração (uma vez só)

Abra o **⚙️** no canto superior e preencha:

| O quê | Onde pegar | Pra quê |
|---|---|---|
| **Provedor de IA** | [Gemini free](https://aistudio.google.com/apikey) (grátis) ou [Navy AI](https://api.navy) (pago) | Resumo, roteiro e escolha de cenas |
| **ElevenLabs — API Key** | [elevenlabs.io](https://elevenlabs.io) | Narração (voz) |
| **ElevenLabs — Voice ID** | Botão "Buscar..." no próprio ⚙️ | Qual voz narra |

💡 O **Gemini free** é a opção de custo zero: pega a key em
[aistudio.google.com/apikey](https://aistudio.google.com/apikey) (login
Google, 30 segundos) e seleciona "Gemini (free)" no topo do ⚙️.

🎵 **Trilhas**: jogue seus `.mp3` na pasta `music/` da instalação
(`%LOCALAPPDATA%\AniRecap\music`). O app sorteia uma por short (ou fixe
uma no botão 🎵).

## 🎬 Como usar

1. **Arrasta o `.mkv`** pra janela (o app extrai a legenda; se o episódio
   tiver faixa de audiodescrição, ele usa pra escolher cenas melhores)
2. **✨ Resumo** → a IA resume o episódio
3. **📝 Short** → vira roteiro de ~60-90s com hook
4. **🎙️ Narração** → ElevenLabs gera a voz
5. **🎬 Plano** → abre o editor visual; revise as cenas e clique
   **Renderizar**
6. O short sai em `%TEMP%\ancopy\work\short_final.mp4` (abre sozinho)

Bônus: **📋 Meta** gera títulos e descrição pro post.

## ⚙️ Como funciona por dentro

```
.mkv ─► extrai legenda (+ audiodescrição) ─► resumo (LLM) ─► roteiro short (LLM)
     ─► narração TTS com timestamps por caractere ─► quebra em beats de 2-3s
     ─► matcher LLM casa cada beat com uma cena (diálogo + descrição visual
        + envelope de áudio + arquétipos narrativos + detecção de rosto)
     ─► editor visual (você revisa) ─► ffmpeg corta, monta 9:16 com blur,
        queima legendas e mixa narração + música
```

Sem GPU, sem modelos pesados locais — roda em qualquer PC. Cache em disco
economiza tokens em re-execuções.

## 📋 Requisitos

- Windows 10/11 64-bit
- Internet (APIs de IA)
- Um `.mkv` com legenda embutida (formato padrão de releases de anime)

## ❓ Problemas comuns

| Sintoma | Solução |
|---|---|
| Antivírus acusa o instalador | Falso positivo comum de apps PyInstaller — adicione exceção |
| "DLL load failed" | Instale o [VC++ Redistributable x64](https://aka.ms/vs/17/release/vc_redist.x64.exe) |
| "Sem créditos / limite atingido" | Free tier do Gemini renova diariamente; ou troque o provedor no ⚙️ |
| Quer saber qual config está em uso | Primeira linha do ⚙️ mostra o caminho do `config.json` |
| Keys sumiram (?) | Não sumiram 🙂 — o app faz backup automático (`config.json.bak`) e restaura sozinho |

## 🛠️ Build a partir do código

Python 3.11+, `pip install -r requirements.txt`, e veja o passo a passo em
[BUILD.md](BUILD.md) (PyInstaller + Inno Setup).

## 📦 Releases

Histórico completo de versões e changelogs em [Releases](../../releases).

---

<div align="center">

Feito por **[@kiumaz](https://github.com/leviclementino1-creator)** · conteúdo de anime 🎌

</div>
