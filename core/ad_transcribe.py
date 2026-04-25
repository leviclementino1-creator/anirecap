"""Transcreve uma track de Audio Description (AD) em cues via faster-whisper.

AD é narração descritiva da cena gravada por humano ("She opens the door
slowly, revealing a dimly lit corridor"). Transformar isso em cues com
timestamps dá pro matcher o sinal visual mais rico possível — muito
melhor que CC (que só marca `[footsteps]` genérico).

Fluxo:
    1. mkvextract track AD → .aac
    2. faster-whisper transcreve → segments com start/end/text
    3. retorna List[Cue] compatível com core.subtitle

Dependência: `faster-whisper` (pip install faster-whisper). O modelo é
baixado on-demand pro cache do HuggingFace (~500MB pro 'medium').
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import requests

from core.cue import Cue
from utils.binaries import find_binary

# Sem janela de console no Windows (quando .exe)
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# Modelo padrão: medium é o melhor trade-off entre qualidade/velocidade pra AD
# (narração clara, sem ruído, vocabulário limitado). 'large-v3' só compensa pra
# áudio complicado. 'small' e 'base' perdem detalhe em frases descritivas.
DEFAULT_MODEL = "medium"


@dataclass
class WhisperResult:
    """Saída da transcrição — cues + idioma detectado."""
    cues: List[Cue]
    language: str  # "en", "fr", "pt" etc (código ISO 639-1)
    duration: float  # duração do áudio em segundos


def transcribe_audio(
    audio_path: str,
    model_name: str = DEFAULT_MODEL,
    device: str = "auto",
    compute_type: str = "auto",
    language_hint: Optional[str] = None,
    progress: Optional[Callable[[float], None]] = None,
) -> WhisperResult:
    """Transcreve um arquivo de áudio com faster-whisper.

    - `device='auto'` → 'cuda' se disponível, senão 'cpu'
    - `compute_type='auto'` → 'float16' em GPU, 'int8' em CPU (mais leve)
    - `language_hint` pode forçar o idioma ('fr', 'en', 'pt') pra acelerar;
      se None, Whisper detecta sozinho.
    - `progress(percent)` é chamado com 0.0→1.0 conforme segmentos saem.

    Retorna WhisperResult (cues + idioma detectado + duração).

    Raises:
        ImportError se faster-whisper não estiver instalado.
        RuntimeError se o arquivo não puder ser aberto.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ImportError(
            "faster-whisper não está instalado. Rode: pip install faster-whisper"
        ) from e

    if not os.path.isfile(audio_path):
        raise RuntimeError(f"Arquivo de áudio não encontrado: {audio_path}")

    # Auto-negocia device/compute_type de forma segura. faster-whisper lança
    # erro se pedirmos float16 em CPU, então só deixa ele escolher.
    if device == "auto":
        device = _pick_device()
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"

    model = WhisperModel(model_name, device=device, compute_type=compute_type)

    # beam_size=5 é o default recomendado; vad_filter remove silêncios longos
    # que costumam gerar alucinações (Whisper "inventa" texto em silêncio).
    segments_iter, info = model.transcribe(
        audio_path,
        language=language_hint,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=False,  # cues por segmento é suficiente pro matcher
    )

    total_duration = float(getattr(info, "duration", 0.0)) or 0.0
    detected_lang = str(getattr(info, "language", "") or "").lower()

    cues: List[Cue] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if not text:
            continue
        start = float(seg.start or 0.0)
        end = float(seg.end or start)
        if end <= start:
            end = start + 0.1
        cues.append(Cue(start=start, end=end, text=text))
        if progress and total_duration > 0:
            try:
                progress(min(1.0, end / total_duration))
            except Exception:
                pass

    return WhisperResult(cues=cues, language=detected_lang, duration=total_duration)


def _pick_device() -> str:
    """Tenta importar torch pra checar CUDA; fallback pra CPU."""
    try:
        import torch  # noqa
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    # faster-whisper também aceita 'cuda' via CTranslate2 sem torch, mas a
    # forma segura de descobrir disponibilidade é via torch. Sem torch, usa CPU.
    return "cpu"


def compress_audio_for_llm(
    src_path: str,
    dst_path: str,
    binaries_dir: str = "",
) -> str:
    """Re-encode audio pra mono 16kHz 32kbps — pronto pra upload em LLM.

    Um episódio de 24min vira ~5-6MB (vs ~22MB do AAC original). Qualidade
    suficiente pra transcrição de fala humana. Reduz 70% do payload HTTP.
    """
    ffmpeg = find_binary("ffmpeg", binaries_dir)
    os.makedirs(os.path.dirname(dst_path) or ".", exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg, "-y", "-i", src_path,
            "-ac", "1",           # mono
            "-ar", "16000",       # 16kHz (ótimo pra fala)
            "-b:a", "32k",        # 32kbps (inteligível pra voz humana)
            "-vn",                # sem vídeo
            dst_path,
        ],
        capture_output=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {result.stderr[:300]}")
    if not os.path.isfile(dst_path):
        raise RuntimeError(f"Áudio comprimido não apareceu em {dst_path}")
    return dst_path


# Prompt otimizado pra transcrição AD com filtragem + tradução embutidas.
AD_EXTRACTION_PROMPT = """\
Você está recebendo o áudio de uma faixa "Descriptive Audio" (audio description)
de um episódio de anime. Essa faixa contém DOIS tipos de som sobrepostos:

1. DIÁLOGO dos personagens do anime (em francês, vozes expressivas, emocionais).
2. NARRAÇÃO DESCRITIVA feita por uma NARRADORA profissional (voz calma,
   uniforme, em primeiro plano nos silêncios entre falas) que descreve
   visualmente o que aparece na tela pra pessoas cegas.

SUA TAREFA:
- Transcreva APENAS a narração descritiva (voz da narradora AD).
- IGNORE completamente o diálogo dos personagens, mesmo se for longo.
- Traduza do francês pro PORTUGUÊS BRASILEIRO natural.
- Use timestamp aproximado `[mm:ss]` no início de cada descrição.
- Uma descrição por linha.
- Se um trecho longo não tiver narração AD (só diálogo/música), PULE. É OK
  haver gaps de minutos inteiros no output.

COMO DISTINGUIR NARRADORA vs PERSONAGEM:
- Narradora AD: voz calma, monótona-descritiva, fala sobre AÇÕES/CENÁRIO
  em terceira pessoa ("Coco ajoelha", "um livro abre", "o dragão ruge").
- Personagem: voz expressiva, emocional, fala em primeira pessoa ("eu vi
  o feiticeiro!", "cuidado!", gritos, risos).

O QUE INCLUIR na transcrição:
- Ações físicas dos personagens ("Coco recua um passo", "Agathe aponta o caderno")
- Expressões faciais/reações ("Coco arregala os olhos", "Tetia se agacha")
- Mudanças de cenário ("a cidade muda", "as paredes ficam brancas")
- Eventos visuais ("uma chama surge", "o dragão pousa as patas com garras")

O QUE NÃO INCLUIR:
- Falas entre aspas ou frases exclamativas de personagens
- Nomes narrados como "Coco disse X" (nunca há discurso citado no AD real)
- Música, efeitos sonoros (a menos que a narradora mencione)

FORMATO DA SAÍDA (obrigatório):
Uma linha por descrição. Formato: `[mm:ss] descrição em português.`

EXEMPLO:
[00:00] Um livro se abre, mostrando os quatro emblemas mágicos.
[00:55] Coco ajoelha diante de uma lareira redonda, desenhando com uma pena.
[01:11] Coco traça um círculo em volta de um emblema triangular.
[01:20] Um jato de água se derrama sobre Coco e a chama.
[01:47] Tetia junta os pés calçados com botas.

SEM markdown, SEM cabeçalhos, SEM preâmbulos. Primeira palavra da saída é `[`.
"""


_TIMESTAMP_LINE_RE = re.compile(r'^\s*\[(\d+):(\d+)(?::(\d+))?\]\s*(.+)$')


def _parse_ad_output(raw: str) -> List[Cue]:
    """Parse output `[mm:ss] texto` → lista de Cue."""
    cues: List[Cue] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _TIMESTAMP_LINE_RE.match(line)
        if not m:
            continue
        g1, g2, g3, text = m.groups()
        if g3 is not None:
            # formato [hh:mm:ss]
            start = int(g1) * 3600 + int(g2) * 60 + int(g3)
        else:
            # formato [mm:ss]
            start = int(g1) * 60 + int(g2)
        cues.append(Cue(start=float(start), end=float(start + 3), text=text.strip()))

    # Ajusta `end` de cada cue pro start da próxima (ou +3s se última).
    for i in range(len(cues) - 1):
        next_start = cues[i + 1].start
        if next_start > cues[i].start:
            cues[i] = Cue(
                start=cues[i].start,
                end=min(next_start, cues[i].start + 8.0),
                text=cues[i].text,
            )
    return cues


def transcribe_audio_via_navy(
    audio_path: str,
    api_key: str,
    base_url: str = "https://api.navy/v1",
    model: str = "gemini-2.5-flash",
    binaries_dir: str = "",
    max_tokens: int = 65000,
    timeout: float = 600.0,
    progress: Optional[Callable[[str], None]] = None,
) -> WhisperResult:
    """
    max_tokens default é 65000 (máximo do Gemini 2.5 Flash). Justificativa:
    - Ep anime típico (24min): ~200 cues × ~15 palavras PT ≈ 4500 tokens
    - Ep longo (40min+): ~350 cues × ~15 palavras ≈ 8000 tokens
    - Filme (90min): ~700 cues × ~15 palavras ≈ 16000 tokens
    - Margem de segurança: 4x sobre filme → 65000 cobre qualquer caso.
    Truncamento silencioso aqui = perder clímax do ep (cenas finais). Melhor
    pagar tokens não-usados do que cortar meio do AD.
    """
    """Transcreve áudio via Navy LLM multimodal (Gemini Flash por default).

    Diferente do Whisper local:
    - ENTENDE contexto: separa narração AD de diálogo na fonte
    - JÁ TRADUZ pro PT (elimina passo posterior)
    - Não precisa de filtro temporal depois (o LLM já ignora diálogo)

    Fluxo:
    1. Re-encode audio pra 32kbps mono 16kHz (reduz upload pra ~6MB)
    2. Encode em base64
    3. Request OpenAI-compat `chat/completions` com content multimodal:
       [{type: text, ...}, {type: input_audio, input_audio: {data, format}}]
    4. Parse output `[mm:ss] texto` → cues
    """
    if not api_key:
        raise RuntimeError("API key vazia — necessária pra Navy AI")
    if not os.path.isfile(audio_path):
        raise RuntimeError(f"Áudio não encontrado: {audio_path}")

    # 1. Compressão
    if progress:
        progress("compressing")
    small_path = audio_path + ".small.m4a"
    try:
        compress_audio_for_llm(audio_path, small_path, binaries_dir=binaries_dir)
    except Exception as e:
        # Se ffmpeg falhar, usa o original mesmo (pode estourar limite de upload)
        small_path = audio_path

    audio_size = os.path.getsize(small_path)
    if progress:
        progress(f"uploading {audio_size // 1024}KB")

    # 2. Base64
    with open(small_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("ascii")

    # 3. Request
    # Formato OpenAI compat (input_audio). Navy normaliza pra Gemini por baixo.
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": AD_EXTRACTION_PROMPT},
            {"type": "input_audio", "input_audio": {
                "data": audio_b64,
                "format": "m4a",
            }},
        ],
    }]

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.encoding = "utf-8"
    if resp.status_code >= 400:
        body = (resp.text or "")[:500]
        raise RuntimeError(f"Navy API {resp.status_code}: {body}")

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Resposta não é JSON: {resp.text[:300]}")

    if "error" in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"Navy API error: {msg}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Resposta sem choices: {str(data)[:300]}")

    finish_reason = choices[0].get("finish_reason", "")
    raw_text = (choices[0].get("message") or {}).get("content") or ""
    if not raw_text.strip():
        raise RuntimeError(
            f"LLM devolveu texto vazio (finish_reason={finish_reason})"
        )

    # Detecta truncamento silencioso. Se Gemini parou por `length`, o AD
    # ficou cortado no meio — perdemos as cenas do fim do ep (geralmente o
    # clímax). Aviso claro em vez de seguir adiante sem saber.
    if finish_reason == "length":
        raise RuntimeError(
            f"AD truncado por max_tokens={max_tokens} "
            f"(finish_reason=length). O ep pode ter mais cenas do que "
            f"cabiam no output. Aumente max_tokens ou quebre em chunks."
        )
    if finish_reason == "content_filter":
        raise RuntimeError(
            "AD cortado pelo content filter do modelo. Tente outro modelo."
        )

    if progress:
        progress("parsing")

    cues = _parse_ad_output(raw_text)
    if not cues:
        # Fallback: talvez LLM não respeitou formato. Mostra raw pra debug.
        raise RuntimeError(
            f"Nenhuma cue parseada. finish_reason={finish_reason}. "
            f"Primeiras 300 chars do raw:\n{raw_text[:300]}"
        )

    duration = cues[-1].end if cues else 0.0
    return WhisperResult(cues=cues, language="pt", duration=duration)


def filter_ad_by_subtitle_gaps(
    ad_cues: List[Cue],
    subtitle_cues: List[Cue],
    overlap_threshold: float = 0.3,
) -> List[Cue]:
    """Remove cues do AD que se sobrepõem temporalmente com a legenda.

    Contexto: em releases tipo Tsundere-Raws, a track "Descriptive" é um
    MIX do áudio original (diálogo do anime) + narração AD. Whisper
    transcreve os dois tipos misturados.

    Mas: a LEGENDA do anime SÓ cobre diálogo dos personagens. Logo, em
    qualquer instante coberto por uma cue de legenda, o Whisper tá
    capturando DIÁLOGO (já temos pela legenda, é redundante). Nos GAPS
    entre legendas, o Whisper tá capturando APENAS o narrador AD.

    Algoritmo:
    - Pra cada cue AD, calcula quanto dela sobrepõe CUE DE LEGENDA.
    - Se overlap_ratio > threshold → é diálogo redundante → descarta.
    - Senão → AD puro → mantém.

    `overlap_threshold=0.3` é permissivo (fala curta que começa dentro
    de uma cue de legenda e termina fora ainda é considerada diálogo).

    Retorna lista filtrada (apenas AD puro).
    """
    if not ad_cues or not subtitle_cues:
        return list(ad_cues)

    sub_sorted = sorted(subtitle_cues, key=lambda c: c.start)

    filtered: List[Cue] = []
    for ad in ad_cues:
        ad_dur = max(0.1, ad.end - ad.start)
        overlap = 0.0
        # Itera só pelas cues de legenda que POSSAM sobrepor
        for s in sub_sorted:
            if s.end < ad.start:
                continue  # cue da legenda acabou antes do AD começar
            if s.start > ad.end:
                break  # cue da legenda começa depois do AD acabar
            lo = max(ad.start, s.start)
            hi = min(ad.end, s.end)
            if hi > lo:
                overlap += hi - lo
        if overlap / ad_dur < overlap_threshold:
            filtered.append(ad)
    return filtered


def audio_fingerprint(audio_path: str) -> str:
    """Hash curto do conteúdo + tamanho do arquivo, pra chave de cache.

    Não precisa ser criptográfico; só precisa mudar se o arquivo mudar.
    Leitura em blocos pra não carregar o arquivo todo na memória.
    """
    h = hashlib.sha1()
    size = os.path.getsize(audio_path)
    h.update(str(size).encode("utf-8"))
    with open(audio_path, "rb") as f:
        # Amostras: início, meio, fim — suficiente pra diferenciar AD tracks.
        for _ in range(64):
            chunk = f.read(4096)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()[:20]


def cues_to_plain_text(cues: List[Cue]) -> str:
    """Converte cues em texto corrido (1 cue por linha), compatível com
    o formato que o resumo/LLM consome."""
    return "\n".join(c.text for c in cues)


def dump_cues_as_srt(cues: List[Cue], output_path: str) -> str:
    """Escreve cues num .srt pra debug/inspeção. Formato idêntico ao padrão."""
    def _ts(s: float) -> str:
        s = max(0.0, s)
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        ms = int(round((s - int(s)) * 1000))
        sec = int(s) % 60
        if ms >= 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{_ts(c.start)} --> {_ts(c.end)}\n{c.text}\n\n")
    return output_path
