import json
import time

import requests

import config
import groq_retry

CLASSIFY_URL = "https://api.groq.com/openai/v1/chat/completions"

RELEVANT_TOPICS = (
    "pensiyalar, sosial müavinətlər, əmək haqqı, işsizlik və məşğulluq, "
    "əlillik və reabilitasiya xidmətləri, DOST mərkəzləri, şəhid ailələri və "
    "müharibə iştirakçılarına dəstək, uşaq və ailə müavinətləri"
)

# Dövlətin inhisarında olan mövzular — bunlarda qurum adı çəkilməsə belə
# mahiyyətcə ƏƏSMN-ə aiddir (pensiyanı/müavinəti verən başqa qurum yoxdur).
STATE_MONOPOLY_TOPICS = (
    "pensiya təyinatı/artımı, dövlət sosial müavinətləri (uşaq, analıq, dəfn, "
    "işsizlik), əlilliyin təyini, şəhid ailəsi və veteran müavinəti, DOST "
    "mərkəzlərinin xidmətləri"
)

# Məşğulluq/əmək mövzuları ise İKİQAT MƏNALI: iş yeri həm dövlət proqramı
# (DMA, DOST), həm də sırf özəl şirkətin öz təşəbbüsü ilə yaranır. Yalnız
# birincisi ƏƏSMN-ə aiddir — bunu ayırd etmək üçün institusional əlaqə şərtdir.
ƏƏSMN_ENTITIES = (
    "Əmək və Əhalinin Sosial Müdafiəsi Nazirliyi, Dövlət Sosial Müdafiə Fondu "
    "(DSMF), Dövlət Məşğulluq Agentliyi (DMA), Dövlət Əmək Müfəttişliyi "
    "Xidməti, Sosial Xidmətlər Agentliyi, Dövlət Tibbi Sığorta və Ekspertiza "
    "Agentliyi, DOST mərkəzləri"
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
    Also classifies the story's sentiment toward ƏƏSMN (MÜSBƏT/MƏNFİ) so the
    clip can enter the PR-monitoring feed (NewsResult) with the same sentiment
    vocabulary the rest of the platform uses.
    Returns {"relevant": bool, "title": str, "sentiment": "MÜSBƏT"|"MƏNFİ",
             "start": float|None, "end": float|None}."""
    display_channel = _display_channel(channel)
    timestamped_transcript = _format_timestamped_transcript(segments)
    prompt = f"""Sən Azərbaycanın Əmək və Əhalinin Sosial Müdafiəsi Nazirliyi (ƏƏSMN) üçün media monitorinqi aparan analitikçisən.
ƏƏSMN-in fəaliyyət sahələri: {RELEVANT_TOPICS}.
ƏƏSMN-in tabeliyində olan qurumlar: {ƏƏSMN_ENTITIES}.

TV sujetinin zaman möhürlü mətni (hər sətir [başlanğıc-son saniyə] formatındadır):
\"\"\"
{timestamped_transcript}
\"\"\"

Tapılan açar söz: "{keyword}"

relevant=true YALNIZ aşağıdakı İKİ ŞƏRTDƏN BİRİ tam ödəndikdə olsun:
(a) Sujetdə ƏƏSMN və ya yuxarıdakı tabeli qurumlardan biri ADI ÇƏKİLƏRƏK birbaşa iştirak edir (nazir, agentlik, DOST mərkəzi və s. rəsmi şəxs/qurum kimi görünür və ya sitat gətirilir).
(b) Mövzu bu dövlət-inhisarlı sahələrdən biridir və başqa izahı yoxdur: {STATE_MONOPOLY_TOPICS} — bu mövzularda qurum adı çəkilməsə belə, mahiyyətcə yalnız dövlət tərəfindən icra oluna bilər, ona görə relevant sayılır.

XÜSUSİ QAYDA — məşğulluq/iş yeri mövzuları: "iş yeri açıldı", "vakansiya", "əmək bazarı", "peşə kursu" kimi mövzular YALNIZ (a) şərti ödəndikdə (yəni DMA, DOST və ya digər ƏƏSMN qurumu konkret iştirak etdikdə) relevant=true olsun. Əgər sujet ÖZƏL şirkətin öz təşəbbüsü ilə iş yeri açması, ümumi iqtisadi statistika, bələdiyyə və ya başqa nazirliyin fəaliyyəti haqqındadırsa (ƏƏSMN qurumu iştirak etmədən) — sırf mövzu oxşarlığı KİFAYƏT DEYİL, relevant=false olmalıdır.

XÜSUSİ QAYDA — bədii məzmun: əgər mətn serial, film, tok-şou, əyləncə proqramı və ya hər hansı quraşdırılmış/bədii dialoqdursa (yəni jurnalist təqdimatlı REAL xəbər süjeti və ya rəsmi açıqlama DEYİL), relevant=false olmalıdır — açar söz (məsələn "analıq", "pensiya", "veteran") mətndə keçsə belə. Personajlar öz aralarında danışırsa, hekayə/emosional kontekst varsa (məsələn "analıq hissi", "o, əsl qazi kimi davrandı") bu, bədii dialoqdur və AİD DEYİL. Yalnız diktor/jurnalist tərəfindən TƏQDİM OLUNAN, real hadisə və ya rəsmi məlumatı çatdıran XƏBƏR SÜJETİ relevant sayıla bilər.

Açar sözün təsadüfən (məsələn tibbi/kommersiya reklamında, aidiyyəti olmayan kontekstdə) keçməsi də AİD DEYİL sayılmalıdır.

Yalnız bu JSON formatında cavab ver, başqa heç nə yazma:
{{"relevant": true ya da false, "title": "...", "sentiment": "MÜSBƏT" ya da "MƏNFİ", "start": 0.0, "end": 0.0}}

relevant=true olarsa:
- "sentiment" sahəsinə sujetin ƏƏSMN/dövlət baxımından tonunu yaz: nailiyyət, artım, yeni xidmət, islahat xəbərləri "MÜSBƏT"; şikayət, tənqid, problem, gecikmə, hüquq pozuntusu xəbərləri "MƏNFİ". Yalnız bu iki dəyərdən birini istifadə et.
- "title" sahəsinə məzmuna əsaslanan, maraqlı, sual formalı Azərbaycan dilində YouTube başlığı yaz, DƏQIQ bu formatda: "Sual formalı başlıq? | {display_channel}"
- "start" sahəsinə bu mövzunun başladığı sətrin başlanğıc saniyəsini (yuxarıdakı [başlanğıc-son] dəyərlərindən BİRİNİ, olduğu kimi) yaz, "end" sahəsinə isə mövzunun bitdiyi sətrin son saniyəsini yaz. Öz ədədini uydurma — mütləq yuxarıdakı sətirlərin dəqiq sərhəd dəyərlərindən istifadə et ki, kəsim cümlənin ortasında deyil, cümlə bitdikdən sonra olsun.
- Mövzudan əvvəlki və sonrakı aidiyyəti olmayan sətirləri DAXİL ETMƏ.
relevant=false olarsa, "title" boş sətir, "start" və "end" 0 olsun.
"""
    last_response = None
    for attempt in range(groq_retry.MAX_RETRIES):
        response = requests.post(
            CLASSIFY_URL,
            headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
            json={
                "model": config.CLASSIFY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
            },
            timeout=60,
        )
        if groq_retry.should_retry(response.status_code) and attempt < groq_retry.MAX_RETRIES - 1:
            last_response = response
            time.sleep(groq_retry.wait_seconds(attempt, response))
            continue
        response.raise_for_status()
        break
    else:
        last_response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    result = json.loads(content)
    start, end = _snap_to_segment_bounds(result.get("start"), result.get("end"), segments)
    sentiment = result.get("sentiment", "")
    if sentiment not in ("MÜSBƏT", "MƏNFİ"):
        sentiment = "MÜSBƏT"  # pipeline müsbət-meyillidir; LLM cavabı pozulsa təhlükəsiz default
    return {
        "relevant": bool(result.get("relevant")),
        "title": result.get("title", ""),
        "sentiment": sentiment,
        "start": start,
        "end": end,
    }
