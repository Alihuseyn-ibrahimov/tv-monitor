import json

import requests

import config

CLASSIFY_URL = "https://api.groq.com/openai/v1/chat/completions"
CLASSIFY_MODEL = "llama-3.3-70b-versatile"

RELEVANT_TOPICS = (
    "pensiyalar, sosial müavinətlər, əmək haqqı, işsizlik və məşğulluq, "
    "əlillik və reabilitasiya xidmətləri, DOST mərkəzləri, şəhid ailələri və "
    "müharibə iştirakçılarına dəstək, uşaq və ailə müavinətləri"
)


def _display_channel(channel: str) -> str:
    return channel if channel.upper().endswith("TV") else f"{channel} TV"


def _format_timestamped_transcript(segments: list[dict]) -> str:
    return "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text'].strip()}" for s in segments)


def _snap_to_segment_bounds(start, end, segments: list[dict]):
    """Whisper already splits the transcript at natural speech pauses, so
    snapping the cut points to the nearest segment boundary (instead of using
    the LLM's raw numbers as-is) guarantees the clip starts/ends between
    sentences rather than mid-sentence."""
    if not segments or start is None or end is None:
        return start, end

    starts_before_or_at = [s["start"] for s in segments if s["start"] <= start]
    snapped_start = max(starts_before_or_at) if starts_before_or_at else segments[0]["start"]

    ends_after_or_at = [s["end"] for s in segments if s["end"] >= end]
    snapped_end = min(ends_after_or_at) if ends_after_or_at else segments[-1]["end"]

    if snapped_end <= snapped_start:
        snapped_start, snapped_end = segments[0]["start"], segments[-1]["end"]
    return snapped_start, snapped_end


def classify_and_title(segments: list[dict], channel: str, keyword: str) -> dict:
    """Asks an LLM whether the segment is genuinely about ƏƏSMN's areas of
    work (not just a coincidental keyword match, e.g. a medical ad mentioning
    "protez"), and if so, generates a YouTube title plus the start/end
    timestamps (seconds, relative to this segment) of just that story — so
    the final clip doesn't include the rest of the 3-minute recording.
    Returns {"relevant": bool, "title": str, "start": float|None, "end": float|None}."""
    display_channel = _display_channel(channel)
    timestamped_transcript = _format_timestamped_transcript(segments)
    prompt = f"""Sən Azərbaycanın Əmək və Əhalinin Sosial Müdafiəsi Nazirliyi (ƏƏSMN) üçün media monitorinqi aparan analitikçisən.
ƏƏSMN-in fəaliyyət sahələri: {RELEVANT_TOPICS}.

TV sujetinin zaman möhürlü mətni (hər sətir [başlanğıc-son saniyə] formatındadır):
\"\"\"
{timestamped_transcript}
\"\"\"

Tapılan açar söz: "{keyword}"

Bu sujet HƏQİQƏTƏN yuxarıdakı sahələrdən birinə aiddirmi? Açar sözün təsadüfən (məsələn tibbi/kommersiya reklamında, aidiyyəti olmayan kontekstdə) keçməsi AİD DEYİL sayılmalıdır.

Yalnız bu JSON formatında cavab ver, başqa heç nə yazma:
{{"relevant": true ya da false, "title": "...", "start": 0.0, "end": 0.0}}

relevant=true olarsa:
- "title" sahəsinə məzmuna əsaslanan, maraqlı, sual formalı Azərbaycan dilində YouTube başlığı yaz, DƏQIQ bu formatda: "Sual formalı başlıq? | {display_channel}"
- "start" sahəsinə bu mövzunun başladığı sətrin başlanğıc saniyəsini (yuxarıdakı [başlanğıc-son] dəyərlərindən BİRİNİ, olduğu kimi) yaz, "end" sahəsinə isə mövzunun bitdiyi sətrin son saniyəsini yaz. Öz ədədini uydurma — mütləq yuxarıdakı sətirlərin dəqiq sərhəd dəyərlərindən istifadə et ki, kəsim cümlənin ortasında deyil, cümlə bitdikdən sonra olsun.
- Mövzudan əvvəlki və sonrakı aidiyyəti olmayan sətirləri DAXİL ETMƏ.
relevant=false olarsa, "title" boş sətir, "start" və "end" 0 olsun.
"""
    response = requests.post(
        CLASSIFY_URL,
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        json={
            "model": CLASSIFY_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        },
        timeout=60,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    result = json.loads(content)
    start, end = _snap_to_segment_bounds(result.get("start"), result.get("end"), segments)
    return {
        "relevant": bool(result.get("relevant")),
        "title": result.get("title", ""),
        "start": start,
        "end": end,
    }
