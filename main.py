import re, json, time, html, urllib.parse, urllib3, requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
session = requests.Session()
session.headers.update({"User-Agent": UA})

SPEECH_FIX = {"secum": "cecum", "seekum": "cecum", "caecum": "cecum", "oesophagus": "esophagus", "oesophageal": "esophageal", "conex": "conecct"}

MEDICAL_DICT = {
    "small": "Nhỏ", "pale": "Nhợt nhạt / Sáng màu", "round": "Tròn", "pit": "Hố tuyến", "pits": "Các hố tuyến",
    "polyp": "Polyp", "polyps": "Các khối polyp", "colon": "Đại tràng", "rectum": "Trực tràng", "sigmoid": "Đại tràng sigma",
    "cecum": "Manh tràng", "secum": "Manh tràng (Cecum)", "ileum": "Hồi tràng", "duodenum": "Tá tràng", "stomach": "Dạ dày",
    "esophagus": "Thực quản", "esophageal": "Thuộc thực quản", "squamous": "Tế bào vảy", "cancer": "Ung thư",
    "carcinoma": "Ung thư biểu mô", "escc": "Ung thư biểu mô tế bào vảy thực quản (ESCC)", "mucosal": "Thuộc niêm mạc",
    "mucosa": "Niêm mạc", "submucosal": "Dưới niêm mạc", "submucosa": "Lớp dưới niêm mạc", "epithelium": "Biểu mô",
    "gland": "Tuyến", "glands": "Các tuyến", "hyperplastic": "Tăng sản", "malignant": "Ác tính", "benign": "Lành tính",
    "dysplasia": "Loạn sản", "neoplasia": "Tân sinh", "invasion": "Xâm lấn", "risk": "Nguy cơ", "minimal": "Tối thiểu",
    "leave it": "Giữ lại", "take it": "Cắt bỏ", "endoscopy": "Nội soi", "lesion": "Tổn thương", "lesions": "Các tổn thương",
    "biopsy": "Sinh thiết", "adenoma": "U tuyến", "colorectal": "Đại trực tràng", "surgery": "Phẫu thuật",
    "chromoendoscopy": "Nội soi nhuộm màu", "nbi": "Nội soi dải tần ánh sáng hẹp (NBI)", "lugol": "Nhuộm Lugol",
    "vessel": "Mạch máu", "vessels": "Các mạch máu", "resection": "Cắt bỏ", "emr": "Cắt hớt niêm mạc (EMR)",
    "esd": "Cắt tách dưới niêm mạc (ESD)", "screening": "Tầm soát", "detection": "Phát hiện", "diagnosis": "Chẩn đoán"
}

class VideoRequest(BaseModel): url: str
class RawSubsRequest(BaseModel):
    video_id: str
    subtitles: list[dict]
class BatchTranslateRequest(BaseModel): texts: list[str]
class WordRequest(BaseModel):
    word: str
    context_sentence: str
    context_vi: str = ""

def extract_youtube_id(url: str):
    m = re.search(r"(?:v=|\/|youtu\.be\/|embed\/|shorts\/)([0-9A-Za-z_-]{11})", url)
    return m.group(1) if m else None

def safe_req(method: str, url: str, **kw):
    kw.setdefault("timeout", 9)
    try: return session.request(method, url, **kw)
    except Exception:
        kw["verify"] = False
        return session.request(method, url, **kw)

def clean_txt(raw: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html.unescape(raw or "")).replace("\n", " ").strip()
    return re.sub(r"\s+", " ", t)

def parse_captions(content: str) -> list[dict]:
    res = []
    if not content or not content.strip(): return res
    c = content.strip()
    if c.startswith("{"):
        try:
            for ev in json.loads(c).get("events", []):
                txt = clean_txt("".join(s.get("utf8", "") for s in ev.get("segs", [])))
                if txt:
                    res.append({"text": txt, "start": round(float(ev.get("tStartMs", 0))/1000, 2), "duration": round(float(ev.get("dDurationMs", 2500))/1000, 2)})
            if res: return res
        except Exception: pass
    for s, d, t in re.findall(r'<text\s+start="([\d\.]+)"(?:\s+dur="([\d\.]+)")?[^>]*>(.*?)</text>', c, re.DOTALL):
        txt = clean_txt(t)
        if txt: res.append({"text": txt, "start": round(float(s), 2), "duration": round(float(d) if d else 2.5, 2)})
    if res: return res
    for s, d, t in re.findall(r'<p\s+t="(\d+)"(?:\s+d="(\d+)")?[^>]*>(.*?)</p>', c, re.DOTALL):
        txt = clean_txt(t)
        if txt: res.append({"text": txt, "start": round(float(s)/1000, 2), "duration": round(float(d)/1000 if d else 2.5, 2)})
    return res

def extract_caption_url_from_html(html_str: str):
    if not html_str or '"captionTracks":' not in html_str: return None
    seg = html_str.split('"captionTracks":')[1].split('"translationLanguages"')[0]
    fallback = None
    for item in seg.split('{"baseUrl":"')[1:]:
        u = item.split('"')[0].replace("\\u0026", "&").replace("\\/", "/")
        if not fallback: fallback = u
        m = re.search(r'"languageCode":"([^"]+)"', item)
        if m and m.group(1).startswith("en"): return u
    return fallback

def fetch_youtube_subtitles(video_id: str) -> list[dict]:
    # Tầng 1: Tactiq API với đầy đủ Origin/Referer header
    for lc in ["en", "en-US", "en-GB"]:
        try:
            r = safe_req("POST", "https://tactiq-apps-prod.tactiq.io/transcript",
                         json={"videoUrl": f"https://www.youtube.com/watch?v={video_id}", "langCode": lc},
                         headers={"Origin": "https://tactiq.io", "Referer": "https://tactiq.io/", "Content-Type": "application/json"})
            if r.status_code == 200 and r.json().get("captions"):
                subs = [{"text": clean_txt(c.get("text", "")), "start": round(float(c.get("start", 0)), 2), "duration": round(float(c.get("dur", 2.5)), 2)} for c in r.json()["captions"] if clean_txt(c.get("text", ""))]
                if subs: return subs
        except Exception: pass

    # Tầng 2: NoteGPT API
    try:
        r = safe_req("GET", f"https://notegpt.io/api/v2/video-transcript?platform=youtube&video_id={video_id}",
                     headers={"Referer": "https://notegpt.io/youtube-transcript-generator"})
        if r.status_code == 200:
            d = r.json().get("data", {})
            tr_dict = d.get("transcripts", {})
            for k in tr_dict:
                items = tr_dict[k].get("custom") or tr_dict[k].get("default") or tr_dict[k].get("auto") or []
                if isinstance(items, list) and items:
                    subs = []
                    for it in items:
                        txt = clean_txt(it.get("text", ""))
                        if txt:
                            st = float(it.get("start", 0) if str(it.get("start", "")).replace(".","").isdigit() else 0)
                            subs.append({"text": txt, "start": round(st, 2), "duration": 2.8})
                    if subs: return subs
    except Exception: pass

    # Tầng 3: Proxy 2 bước đồng bộ chữ ký IP (CodeTabs, AllOrigins, CorsProxy)
    watch_url = f"https://www.youtube.com/watch?v={video_id}&hl=en"
    relays = [
        lambda u: f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(u, safe='')}",
        lambda u: f"https://api.allorigins.win/raw?url={urllib.parse.quote(u, safe='')}",
        lambda u: f"https://corsproxy.io/?url={urllib.parse.quote(u, safe='')}"
    ]
    for make_relay in relays:
        try:
            r1 = safe_req("GET", make_relay(watch_url), timeout=10)
            if r1.status_code == 200 and '"captionTracks":' in r1.text:
                cap_url = extract_caption_url_from_html(r1.text)
                if cap_url:
                    r2 = safe_req("GET", make_relay(cap_url + "&fmt=json3"), timeout=10)
                    subs = parse_captions(r2.text)
                    if not subs:
                        r3 = safe_req("GET", make_relay(cap_url), timeout=10)
                        subs = parse_captions(r3.text)
                    if subs: return subs
        except Exception: pass

    # Tầng 4: YouTube TV Embedded & Web Embedded Client
    for c_name, c_ver in [("TVHTML5_SIMPLY_EMBEDDED_PLAYER", "2.0"), ("WEB_EMBEDDED_PLAYER", "1.20250101.01.00")]:
        try:
            ctx = {"client": {"clientName": c_name, "clientVersion": c_ver, "hl": "en", "gl": "US"}, "thirdParty": {"embedUrl": "https://www.youtube.com/"}}
            r = safe_req("POST", "https://www.youtube.com/youtubei/v1/player?prettyPrint=false", json={"videoId": video_id, "context": ctx})
            if r.status_code == 200 and '"captionTracks":' in r.text:
                cap_url = extract_caption_url_from_html(r.text)
                if cap_url:
                    subs = parse_captions(safe_req("GET", cap_url + "&fmt=json3").text) or parse_captions(safe_req("GET", cap_url).text)
                    if subs: return subs
        except Exception: pass

    raise RuntimeError("NEED_CLIENT_FETCH")

def clean_fillers(t: str) -> str:
    c = re.sub(r"\s+", " ", re.sub(r"\b(uh+|um+|er+|erm+|ah+|hmm+)\b", "", t, flags=re.I)).strip()
    return c if c else t.strip()

def raw_gtrans(block: str) -> str:
    if not block or not block.strip(): return block
    for url, client in [("https://translate.googleapis.com/translate_a/single", "gtx"), ("https://clients5.google.com/translate_a/t", "dict-chrome-ex")]:
        try:
            r = safe_req("POST", url, params={"client": client, "sl": "en", "tl": "vi", "dt": "t"}, data={"q": block})
            if r.status_code == 200:
                d = r.json()
                if client == "gtx" and d and d[0]:
                    return "".join([p[0] for p in d[0] if p and p[0]]).strip()
                elif isinstance(d, list) and d:
                    return (d[0] if isinstance(d[0], str) else d[0][0]).strip()
        except Exception: pass
    return block

def translate_single(t: str) -> str:
    c = clean_fillers(t.strip())
    low = SPEECH_FIX.get(c.lower().rstrip("."), c.lower().rstrip("."))
    if low in MEDICAL_DICT: return MEDICAL_DICT[low]
    res = raw_gtrans(c)
    return res if res else t

def translate_batch_list(texts: list[str], batch_size: int = 35) -> list[str]:
    out = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        lines = [f"[{idx}] {clean_fillers(x) + ('.' if clean_fillers(x)[-1:] not in '.?!' else '')}" for idx, x in enumerate(chunk)]
        trans_block = raw_gtrans("\n".join(lines))
        rmap = {int(k): v.strip().rstrip(".") for k, v in re.findall(r"\[\s*(\d+)\s*\]\s*(.*?)(?=\[\s*\d+\s*\]|$)", trans_block, re.DOTALL)}
        for idx, orig in enumerate(chunk):
            low = orig.lower().strip().rstrip(".")
            if low in MEDICAL_DICT: out.append(MEDICAL_DICT[low])
            else:
                val = rmap.get(idx, "")
                out.append(val if (val and val.lower() != clean_fillers(orig).lower()) else translate_single(orig))
        if i + batch_size < len(texts): time.sleep(0.08)
    return out

def prepare_initial_subs(vid: str, raw_subs: list[dict]):
    subs = raw_subs[:3000]
    for s in subs: s["text"] = clean_txt(s.get("text", "")) or "..."
    init_n = min(100, len(subs))
    init_vi = translate_batch_list([subs[i]["text"] for i in range(init_n)])
    for i, s in enumerate(subs):
        s["vi"] = init_vi[i] if i < init_n else "⏳ Đang dịch tự động..."
        s["translated"] = (i < init_n)
    return {"video_id": vid, "subtitles": subs}

@app.get("/api/tts")
async def tts(text: str = Query(..., max_length=500)):
    c = text.replace("⏳", "").strip()[:195]
    if not c: return Response(status_code=204)
    try:
        r = safe_req("GET", "https://translate.googleapis.com/translate_tts", params={"ie": "UTF-8", "q": c, "tl": "vi", "client": "tw-ob"})
        if r.status_code == 200 and r.content:
            return Response(content=r.content, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})
    except Exception: pass
    return Response(status_code=502)

@app.post("/api/process-video")
async def process_video(req: VideoRequest):
    vid = extract_youtube_id(req.url)
    if not vid: return {"error": "Link YouTube không hợp lệ!"}
    try:
        subs = fetch_youtube_subtitles(vid)
        return prepare_initial_subs(vid, subs)
    except Exception:
        return {"need_client_fetch": True, "video_id": vid}

@app.post("/api/process-raw-subtitles")
async def process_raw_subtitles(req: RawSubsRequest):
    if not req.subtitles: return {"error": "Không tìm thấy phụ đề tiếng Anh trên video này."}
    return prepare_initial_subs(req.video_id, req.subtitles)

@app.post("/api/translate-batch")
async def api_translate_batch(req: BatchTranslateRequest):
    return {"translations": translate_batch_list(req.texts, batch_size=40)}

@app.post("/api/lookup-word")
async def lookup_word(req: WordRequest):
    w = req.word.strip()
    low = w.lower()
    cands = [SPEECH_FIX.get(low, low), low]
    for base in list(cands):
        if base.endswith("ies"): cands.append(base[:-3] + "y")
        if base.endswith("es"): cands.append(base[:-2])
        if base.endswith("s") and not base.endswith("ss"): cands.append(base[:-1])
        if base.endswith("ed"): cands.extend([base[:-2], base[:-1]])
        if base.endswith("ing"): cands.extend([base[:-3], base[:-3] + "e"])

    ipa, pos, en_def, audio = "...", "Từ vựng", "", ""
    for c in dict.fromkeys(cands):
        try:
            r = safe_req("GET", f"https://api.dictionaryapi.dev/api/v2/entries/en/{c}", timeout=4)
            if r.status_code == 200:
                d = r.json()[0]
                ipa = d.get("phonetic", "").strip("/") or next((p.get("text", "").strip("/") for p in d.get("phonetics", []) if p.get("text")), "...")
                audio = next((p.get("audio") for p in d.get("phonetics", []) if p.get("audio")), "")
                if d.get("meanings"):
                    pos = d["meanings"][0].get("partOfSpeech", "Từ vựng")
                    en_def = d["meanings"][0]["definitions"][0].get("definition", "")
                break
        except Exception: pass

    med = next((MEDICAL_DICT[c] for c in cands if c in MEDICAL_DICT), None)
    if med: pos = "Thuật ngữ Y khoa"
    gen = translate_single(SPEECH_FIX.get(low, w))
    vi_w = f"{med} ({gen})" if (med and gen.lower() not in med.lower() and gen.lower() != low) else (med or gen)
    vi_sen = req.context_vi if (req.context_vi and "⏳" not in req.context_vi) else translate_single(req.context_sentence)
    exp = (f"• Định nghĩa: {en_def}\n" if en_def else "") + f"• Dịch câu: \"{vi_sen}\""
    return {"word": w, "ipa": ipa, "pos": pos, "meaning": vi_w, "explanation": exp, "audio": audio}

HTML_CONTENT = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>MedLingua AI - Dịch Cabin & Học Tiếng Anh</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    .word-span { cursor: pointer; padding: 2px 4px; border-radius: 4px; display: inline-block; }
    .word-span:hover, .word-span:active { background: #fde047; color: #000; font-weight: 600; }
    .active-sub { background: #1e3a8a !important; border-left: 4px solid #38bdf8 !important; }
    .cabin-glow { box-shadow: 0 0 15px rgba(16,185,129,0.35); border-color: #10b981 !important; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen pb-6 lg:p-6">
  <div class="max-w-7xl mx-auto px-2 sm:px-4">
    <header class="border-b border-slate-800 py-3 mb-3 flex justify-between items-center">
      <h1 class="text-lg sm:text-2xl font-bold text-blue-400">🎬 MedLingua AI <span class="text-xs bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded-full">Cabin • Mobile & PC</span></h1>
    </header>

    <div class="bg-slate-900 border border-slate-800 p-2.5 rounded-xl mb-3 flex gap-2">
      <input id="videoUrl" type="text" placeholder="Dán link YouTube vào đây..." class="flex-1 p-2.5 rounded-lg bg-slate-950 border border-slate-700 text-white text-xs sm:text-sm min-w-0">
      <button onclick="analyzeVideo()" id="btnLoad" class="bg-blue-600 hover:bg-blue-500 px-4 py-2.5 rounded-lg font-semibold text-xs sm:text-sm shrink-0">⚡ Dịch Video</button>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 lg:gap-6">
      <div class="lg:col-span-2 space-y-2.5 sticky top-0 z-30 bg-slate-950 pt-1 pb-2 lg:static">
        <div class="aspect-video bg-black rounded-xl overflow-hidden border border-slate-800"><div id="player" class="w-full h-full"></div></div>

        <div id="cabinPanel" class="p-2.5 sm:p-3.5 rounded-xl bg-slate-900 border border-slate-800">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <button id="btnCabinToggle" onclick="toggleCabinMode()" class="w-full sm:w-auto justify-center bg-slate-800 text-slate-200 border border-slate-700 px-3.5 py-2 rounded-xl font-bold text-xs sm:text-sm flex items-center gap-2">
              <span id="cabinDot" class="w-2.5 h-2.5 rounded-full bg-slate-500"></span>
              <span id="cabinBtnText">🎧 Bật Dịch Cabin (Nghe Song Song)</span>
            </button>
            <div class="flex items-center justify-between w-full sm:w-auto gap-2 text-xs">
              <div class="flex items-center gap-1.5 bg-slate-950 px-2.5 py-1.5 rounded-lg border border-slate-800">
                <span>🔊 Âm gốc:</span>
                <input type="range" min="5" max="70" value="20" oninput="updateOrigVol(this.value)" class="w-16 accent-blue-500">
                <span id="origVolValue" class="text-blue-400 font-bold">20%</span>
              </div>
              <select onchange="cabinRate=parseFloat(this.value);cabinAudio.playbackRate=cabinRate;" class="bg-slate-950 text-emerald-400 font-bold px-2 py-1.5 rounded-lg border border-slate-800">
                <option value="1.05">1.0x</option><option value="1.2" selected>1.2x</option><option value="1.35">1.35x</option>
              </select>
            </div>
          </div>
          <div id="cabinLiveBar" class="hidden mt-2 pt-2 border-t border-slate-800 flex items-center gap-2 text-xs">
            <span class="bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded font-semibold shrink-0">🎙️ Cabin</span>
            <p id="cabinLiveText" class="text-emerald-400 truncate">...</p>
          </div>
        </div>

        <div id="wordBox" class="hidden fixed bottom-0 left-0 right-0 z-50 p-4 rounded-t-2xl bg-slate-900 border-t-2 border-blue-500 shadow-2xl lg:static lg:rounded-xl lg:border lg:border-slate-800">
          <div class="flex justify-between items-start border-b border-slate-800 pb-2 mb-2">
            <div>
              <span id="wText" class="text-xl font-bold text-yellow-400"></span>
              <span id="wIpa" class="text-slate-400 font-mono text-xs ml-2"></span>
              <span id="wPos" class="text-xs bg-slate-800 text-blue-300 px-2 py-0.5 rounded-full ml-1"></span>
              <p id="wMeaning" class="text-lg text-emerald-400 font-bold mt-1"></p>
            </div>
            <button onclick="document.getElementById('wordBox').classList.add('hidden')" class="bg-slate-800 w-7 h-7 rounded-full text-sm font-bold">✕</button>
          </div>
          <p id="wExplain" class="text-xs sm:text-sm text-slate-300 whitespace-pre-line mb-3"></p>
          <div class="flex gap-2">
            <button id="btnDictAudio" onclick="dictAudio.play()" class="hidden bg-emerald-600/20 text-emerald-300 px-3 py-2 rounded-lg text-xs font-semibold">🎙️ Nghe MP3</button>
            <button onclick="playCurrentSentence()" class="bg-blue-600 text-white px-4 py-2 rounded-lg text-xs font-semibold">🔊 Nghe câu gốc</button>
          </div>
        </div>
      </div>

      <div class="bg-slate-900 rounded-xl border border-slate-800 p-3 h-[52vh] lg:h-[640px] flex flex-col">
        <div class="border-b border-slate-800 pb-2 mb-2 flex justify-between items-center">
          <span class="font-bold text-xs sm:text-sm">PHỤ ĐỀ SONG NGỮ TƯƠNG TÁC</span>
          <span id="subCount" class="text-xs bg-slate-800 text-emerald-400 px-2 py-0.5 rounded"></span>
        </div>
        <div id="subtitleList" class="flex-1 overflow-y-auto space-y-2 pr-1 relative"></div>
      </div>
    </div>
  </div>

  <script src="https://www.youtube.com/iframe_api"></script>
  <script>
    let player, subtitles = [], activeTimer = null, activeSubIndex = 0, jobId = 0;
    let cabinEnabled = false, isSpeaking = false, lastSpokenIdx = -1, duckVol = 20, cabinRate = 1.2;
    const cabinAudio = new Audio(), dictAudio = new Audio();
    cabinAudio.playsInline = true; dictAudio.playsInline = true;

    function onYouTubeIframeAPIReady() { processUrlOrId("https://www.youtube.com/watch?v=gedoSfZvBgE"); }

    function parseCapData(str) {
      const res = [];
      if (!str || !str.trim()) return res;
      if (str.trim().startsWith("{")) {
        try {
          const evs = JSON.parse(str).events || [];
          for (const ev of evs) {
            const txt = (ev.segs || []).map(s => s.utf8 || "").join("").replace(/\n/g, " ").trim();
            if (txt) res.push({text: txt, start: +(ev.tStartMs/1000).toFixed(2), duration: +((ev.dDurationMs||2500)/1000).toFixed(2)});
          }
          if (res.length) return res;
        } catch(e){}
      }
      const doc = new DOMParser().parseFromString(str, "text/xml");
      const nodes = doc.querySelectorAll("text, p");
      nodes.forEach(n => {
        const txt = (n.textContent || "").replace(/\n/g, " ").trim();
        if (!txt) return;
        const st = n.hasAttribute("start") ? parseFloat(n.getAttribute("start")) : parseFloat(n.getAttribute("t")||0)/1000;
        const du = n.hasAttribute("dur") ? parseFloat(n.getAttribute("dur")) : parseFloat(n.getAttribute("d")||2500)/1000;
        res.push({text: txt, start: +st.toFixed(2), duration: +(du||2.5).toFixed(2)});
      });
      return res;
    }

    async function fetchFromPhoneBrowser(vid) {
      // 1. Thử Tactiq trực tiếp từ mạng 4G/Wi-Fi của điện thoại
      try {
        const r = await fetch("https://tactiq-apps-prod.tactiq.io/transcript", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({videoUrl: "https://www.youtube.com/watch?v=" + vid, langCode: "en"})
        });
        if (r.ok) {
          const d = await r.json();
          if (d.captions && d.captions.length) {
            return d.captions.map(c => ({text: c.text, start: +parseFloat(c.start||0).toFixed(2), duration: +parseFloat(c.dur||2.5).toFixed(2)}));
          }
        }
      } catch(e){}

      // 2. Thử lấy qua Proxy đồng bộ chữ ký IP từ trình duyệt điện thoại
      const wUrl = "https://www.youtube.com/watch?v=" + vid + "&hl=en";
      const proxies = [
        u => "https://api.codetabs.com/v1/proxy?quest=" + encodeURIComponent(u),
        u => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u),
        u => "https://corsproxy.io/?url=" + encodeURIComponent(u)
      ];
      for (const px of proxies) {
        try {
          const r1 = await fetch(px(wUrl));
          const html = await r1.text();
          if (html.includes('"captionTracks":')) {
            const seg = html.split('"captionTracks":')[1].split('"translationLanguages"')[0];
            const parts = seg.split('{"baseUrl":"').slice(1);
            let capUrl = null;
            for (const p of parts) {
              const u = p.split('"')[0].replace(/\\u0026/g, "&").replace(/\\\//g, "/");
              if (!capUrl) capUrl = u;
              if (p.includes('"languageCode":"en')) { capUrl = u; break; }
            }
            if (capUrl) {
              const r2 = await fetch(px(capUrl + "&fmt=json3"));
              let subs = parseCapData(await r2.text());
              if (!subs.length) {
                const r3 = await fetch(px(capUrl));
                subs = parseCapData(await r3.text());
              }
              if (subs.length) return subs;
            }
          }
        } catch(e){}
      }
      return [];
    }

    async function processUrlOrId(url) {
      const r = await fetch("/api/process-video", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({url})});
      let d = await r.json();
      if (d.need_client_fetch && d.video_id) {
        initPlayer(d.video_id);
        const rawSubs = await fetchFromPhoneBrowser(d.video_id);
        if (rawSubs.length) {
          const r2 = await fetch("/api/process-raw-subtitles", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({video_id: d.video_id, subtitles: rawSubs})});
          d = await r2.json();
        } else {
          d = {error: "Video này không bật phụ đề (CC) hoặc tác giả đã tắt phụ đề."};
        }
      }
      if (d.error) alert(d.error);
      else if (d.subtitles) {
        subtitles = d.subtitles; lastSpokenIdx = -1; initPlayer(d.video_id); renderSubs(); startBgTrans();
      }
    }

    function initPlayer(vid) {
      if (player && player.loadVideoById) player.loadVideoById(vid);
      else player = new YT.Player('player', {height:'100%', width:'100%', videoId:vid, playerVars:{playsinline:1, rel:0}, events:{onStateChange: e => { if(e.data===2||e.data===0) stopCabin(); }}});
    }

    function updateOrigVol(v) {
      duckVol = parseInt(v, 10);
      document.getElementById("origVolValue").innerText = duckVol + "%";
      if (cabinEnabled && player && player.setVolume) player.setVolume(duckVol);
    }

    function toggleCabinMode() {
      cabinEnabled = !cabinEnabled;
      const btn = document.getElementById("btnCabinToggle"), txt = document.getElementById("cabinBtnText");
      const panel = document.getElementById("cabinPanel"), bar = document.getElementById("cabinLiveBar");
      if (cabinEnabled) {
        cabinAudio.src = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA";
        cabinAudio.play().then(()=>{cabinAudio.pause();}).catch(()=>{});
        btn.className = "w-full sm:w-auto justify-center bg-emerald-600 text-white px-3.5 py-2 rounded-xl font-bold text-xs sm:text-sm flex items-center gap-2";
        txt.innerText = "🎧 Đang Bật Dịch Cabin (Chạm để tắt)";
        panel.classList.add("cabin-glow"); bar.classList.remove("hidden");
        lastSpokenIdx = activeSubIndex - 1;
        if (player && player.setVolume) player.setVolume(duckVol);
        triggerCabin();
      } else {
        btn.className = "w-full sm:w-auto justify-center bg-slate-800 text-slate-200 border border-slate-700 px-3.5 py-2 rounded-xl font-bold text-xs sm:text-sm flex items-center gap-2";
        txt.innerText = "🎧 Bật Dịch Cabin (Nghe Song Song)";
        panel.classList.remove("cabin-glow"); bar.classList.add("hidden");
        stopCabin();
        if (player && player.setVolume) player.setVolume(100);
      }
    }

    function stopCabin() { try { cabinAudio.pause(); } catch(e){} isSpeaking = false; }

    function speakText(viText, cb) {
      if (!viText || viText.includes("⏳")) { if (cb) cb(); return; }
      stopCabin(); isSpeaking = true;
      document.getElementById("cabinLiveText").innerText = viText;
      if (player && player.setVolume) player.setVolume(duckVol);
      cabinAudio.src = "/api/tts?text=" + encodeURIComponent(viText);
      cabinAudio.playbackRate = cabinRate;
      cabinAudio.onended = () => { isSpeaking = false; if (cb) cb(); };
      cabinAudio.onerror = () => { isSpeaking = false; if (cb) cb(); };
      cabinAudio.play().catch(() => { isSpeaking = false; if (cb) cb(); });
    }

    function triggerCabin() {
      if (!cabinEnabled || isSpeaking || !player || !player.getPlayerState || player.getPlayerState() !== 1) return;
      if (activeSubIndex > lastSpokenIdx && subtitles[activeSubIndex]) {
        let idx = activeSubIndex, txt = subtitles[idx].vi || "";
        if (txt.length < 45 && idx + 1 < subtitles.length && subtitles[idx + 1].translated) {
          txt += " " + subtitles[idx + 1].vi; idx++;
        }
        lastSpokenIdx = idx;
        speakText(txt, () => { if (cabinEnabled) setTimeout(triggerCabin, 80); });
      }
    }

    async function analyzeVideo() {
      const url = document.getElementById("videoUrl").value.trim();
      if (!url) return alert("Hãy dán link YouTube!");
      const btn = document.getElementById("btnLoad");
      btn.innerText = "⏳ Đang tải..."; btn.disabled = true; stopCabin();
      try { await processUrlOrId(url); }
      catch (e) { alert("Lỗi kết nối: " + e.message); }
      finally { btn.innerText = "⚡ Dịch Video"; btn.disabled = false; }
    }

    async function startBgTrans() {
      const cur = ++jobId;
      while (cur === jobId) {
        const todo = [];
        for (let i = 0; i < subtitles.length && todo.length < 40; i++) {
          const idx = (activeSubIndex + i) % subtitles.length;
          if (!subtitles[idx].translated) todo.push(idx);
        }
        const done = subtitles.filter(s => s.translated).length;
        document.getElementById("subCount").innerText = done < subtitles.length ? `⚡ ${done}/${subtitles.length}` : `${subtitles.length} câu`;
        if (!todo.length) break;
        try {
          const r = await fetch("/api/translate-batch", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({texts: todo.map(i => subtitles[i].text)})});
          const d = await r.json();
          if (cur !== jobId) return;
          (d.translations || []).forEach((vi, k) => {
            const idx = todo[k]; subtitles[idx].vi = vi; subtitles[idx].translated = true;
            const el = document.getElementById("sub-vi-" + idx); if (el) el.innerText = vi;
          });
        } catch (e) { await new Promise(r => setTimeout(r, 1000)); }
      }
    }

    function renderSubs() {
      const box = document.getElementById("subtitleList"); box.innerHTML = "";
      subtitles.forEach((s, i) => {
        const div = document.createElement("div");
        div.id = "sub-" + i;
        div.className = "p-2.5 rounded-lg bg-slate-950/70 border border-slate-800 cursor-pointer";
        const words = s.text.split(" ").map(w => `<span class="word-span" onclick="lookupWord(event,'${w.replace(/[^a-zA-Z0-9-]/g,"")}',${i})">${w}</span>`).join(" ");
        div.innerHTML = `<div class="flex justify-between items-start gap-2"><div class="text-sm sm:text-base font-medium leading-relaxed">${words}</div><div class="flex gap-1 shrink-0"><button onclick="event.stopPropagation();playCabinOne(${i})" class="text-xs bg-emerald-600/20 text-emerald-300 px-2 py-1 rounded">🎙️ Cabin</button><button onclick="event.stopPropagation();playSeg(${s.start},${s.duration},${i})" class="text-xs bg-slate-800 text-slate-300 px-2 py-1 rounded">🔊 Gốc</button></div></div><div id="sub-vi-${i}" class="text-xs sm:text-sm text-emerald-400 font-medium mt-1">${s.vi}</div>`;
        div.onclick = () => playSeg(s.start, s.duration, i);
        box.appendChild(div);
      });
    }

    function playSeg(start, dur, i) {
      activeSubIndex = i; stopCabin();
      if (player && player.seekTo) {
        player.setVolume(100); player.seekTo(start, true); player.playVideo();
        if (activeTimer) clearTimeout(activeTimer);
        activeTimer = setTimeout(() => player.pauseVideo(), (dur + 0.25) * 1000);
      }
    }

    function playCabinOne(i) {
      const s = subtitles[i]; if (!s) return;
      activeSubIndex = i; if (activeTimer) clearTimeout(activeTimer);
      if (player && player.seekTo) { player.setVolume(duckVol); player.seekTo(s.start, true); player.playVideo(); }
      document.getElementById("cabinLiveBar").classList.remove("hidden");
      speakText(s.vi, () => { if (!cabinEnabled && player) player.setVolume(100); });
      activeTimer = setTimeout(() => { if (!cabinEnabled && player) { player.pauseVideo(); player.setVolume(100); } }, Math.max(s.duration + 0.8, 3.5) * 1000);
    }

    function playCurrentSentence() { const s = subtitles[activeSubIndex]; if (s) playSeg(s.start, s.duration, activeSubIndex); }

    let lastHl = -1;
    setInterval(() => {
      if (!player || !player.getCurrentTime) return;
      const t = player.getCurrentTime();
      const idx = subtitles.findIndex(s => t >= s.start && t <= s.start + s.duration);
      if (idx !== -1 && idx !== lastHl) {
        if (lastHl !== -1 && document.getElementById("sub-" + lastHl)) document.getElementById("sub-" + lastHl).classList.remove("active-sub");
        const el = document.getElementById("sub-" + idx), box = document.getElementById("subtitleList");
        if (el && box) { el.classList.add("active-sub"); box.scrollTo({top: Math.max(0, el.offsetTop - box.offsetTop - box.clientHeight/3), behavior:"smooth"}); }
        if (idx < lastSpokenIdx || idx - lastSpokenIdx > 4) lastSpokenIdx = idx - 1;
        activeSubIndex = idx; lastHl = idx;
        if (cabinEnabled) triggerCabin();
      }
    }, 200);

    async function lookupWord(ev, word, idx) {
      ev.stopPropagation(); if (!word) return;
      const s = subtitles[idx]; playSeg(s.start, s.duration, idx);
      document.getElementById("wordBox").classList.remove("hidden");
      document.getElementById("wText").innerText = word;
      document.getElementById("wMeaning").innerText = "Đang dịch...";
      document.getElementById("btnDictAudio").classList.add("hidden");
      const r = await fetch("/api/lookup-word", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({word, context_sentence:s.text, context_vi:s.vi})});
      const d = await r.json();
      document.getElementById("wText").innerText = d.word;
      document.getElementById("wIpa").innerText = "/" + d.ipa + "/";
      document.getElementById("wPos").innerText = d.pos;
      document.getElementById("wMeaning").innerText = d.meaning;
      document.getElementById("wExplain").innerText = d.explanation;
      if (d.audio) { dictAudio.src = d.audio; document.getElementById("btnDictAudio").classList.remove("hidden"); }
    }
  </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def serve_home():
    return HTMLResponse(content=HTML_CONTENT, status_code=200)
