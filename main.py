import re, json, time, html, urllib.parse, urllib3, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
session.cookies.set("CONSENT", "YES+cb.20210328-17-p0.en+FX+410", domain=".youtube.com")
session.cookies.set("SOCS", "CAISHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiAo_CmBg", domain=".youtube.com")

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
    "esd": "Cắt tách dưới niêm mạc (ESD)", "screening": "Tầm soát", "detection": "Phát hiện", "diagnosis": "Chẩn đoán",
    "knot": "Nút buộc chỉ", "suture": "Chỉ khâu phẫu thuật", "tie": "Buộc / Thắt nút", "forceps": "Kẹp phẫu thuật"
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
    if not url: return None
    u = url.strip()
    # Hỗ trợ đầy đủ: watch?v=, youtu.be/, live/, shorts/, embed/, v/
    m = re.search(r"(?:v=|youtu\.be\/|embed\/|shorts\/|live\/|\/v\/)([0-9A-Za-z_-]{11})", u)
    if m: return m.group(1)
    # Nếu người dùng dán trực tiếp chuỗi có chứa ID 11 ký tự
    m2 = re.search(r"(?:^|[^0-9A-Za-z_-])([0-9A-Za-z_-]{11})(?:$|[^0-9A-Za-z_-])", u)
    return m2.group(1) if m2 else None

def safe_req(method: str, url: str, **kw):
    kw.setdefault("timeout", 12)
    try: return session.request(method, url, **kw)
    except Exception:
        kw["verify"] = False
        return session.request(method, url, **kw)

def clean_txt(raw: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html.unescape(raw or "")).replace("\n", " ").strip()
    return re.sub(r"\s+", " ", t)

def to_sec(val) -> float:
    if isinstance(val, (int, float)): return float(val)
    s = str(val or "0").strip().replace(",", ".")
    if ":" in s:
        p = s.split(":")
        if len(p) == 3: return int(p[0])*3600 + int(p[1])*60 + float(p[2])
        if len(p) == 2: return int(p[0])*60 + float(p[1])
    try: return float(s)
    except Exception: return 0.0

def parse_captions(content: str) -> list[dict]:
    res = []
    if not content or not content.strip(): return res
    c = content.strip()
    if "WEBVTT" in c[:30] or "-->" in c[:200]:
        for block in re.split(r"\n\s*\n", c.replace("\r\n", "\n")):
            lines = [x.strip() for x in block.split("\n") if x.strip()]
            t_idx = next((i for i, l in enumerate(lines) if "-->" in l), -1)
            if t_idx != -1:
                pts = lines[t_idx].split("-->")
                st, et = to_sec(pts[0]), to_sec(pts[1].split()[0])
                txt = clean_txt(" ".join(lines[t_idx+1:]))
                if txt and (not res or res[-1]["text"] != txt):
                    res.append({"text": txt, "start": round(st, 2), "duration": max(round(et - st, 2), 1.5)})
        if res: return res
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

def extract_cap_url(html_str: str):
    if not html_str or '"captionTracks":' not in html_str: return None
    m = re.search(r'"captionTracks":\s*(\[.*?\])', html_str)
    if m:
        try:
            tracks = json.loads(m.group(1))
            if tracks:
                tr = next((x for x in tracks if x.get("languageCode", "").startswith("en")), tracks[0])
                return tr.get("baseUrl")
        except Exception: pass
    return None

def w_tactiq(vid: str):
    for lc in ["en", "en-US", "en-GB"]:
        r = safe_req("POST", "https://tactiq-apps-prod.tactiq.io/transcript",
                     json={"videoUrl": f"https://www.youtube.com/watch?v={vid}", "langCode": lc},
                     headers={"Origin": "https://tactiq.io", "Referer": "https://tactiq.io/", "Content-Type": "application/json"}, timeout=12)
        if r.status_code == 200 and r.json().get("captions"):
            subs = [{"text": clean_txt(c.get("text", "")), "start": round(to_sec(c.get("start", 0)), 2), "duration": round(to_sec(c.get("dur", 2.5)), 2)} for c in r.json()["captions"] if clean_txt(c.get("text", ""))]
            if subs: return subs
    return []

def w_notegpt(vid: str):
    r = safe_req("GET", f"https://notegpt.io/api/v2/video-transcript?platform=youtube&video_id={vid}",
                 headers={"Referer": "https://notegpt.io/youtube-transcript-generator"}, timeout=12)
    if r.status_code == 200:
        tr_dict = r.json().get("data", {}).get("transcripts", {})
        for k in tr_dict:
            items = tr_dict[k].get("custom") or tr_dict[k].get("default") or tr_dict[k].get("auto") or []
            if isinstance(items, list) and items:
                subs = []
                for it in items:
                    txt = clean_txt(it.get("text", ""))
                    if txt:
                        st = to_sec(it.get("start", 0))
                        et = to_sec(it.get("end", st + 2.5))
                        subs.append({"text": txt, "start": round(st, 2), "duration": max(round(et - st, 2), 1.5)})
                if subs: return subs
    return []

def w_yttrans(vid: str):
    r = safe_req("GET", f"https://youtubetranscript.com/?server_vid2={vid}", timeout=12)
    if r.status_code == 200 and "<text" in r.text:
        return parse_captions(r.text)
    return []

def w_innertube(vid: str):
    clients = [
        {"client": {"clientName": "ANDROID_VR", "clientVersion": "1.61.48", "androidSdkVersion": 34, "userAgent": "com.google.android.apps.youtube.vr.oculus/1.61.48 (Linux; U; Android 14; en_US; Quest 3; Build/UP1A.231005.007.A1) gzip", "hl": "en", "gl": "US"}},
        {"client": {"clientName": "IOS", "clientVersion": "20.03.02", "deviceMake": "Apple", "deviceModel": "iPhone16,2", "userAgent": "com.google.ios.youtube/20.03.02 (iPhone16,2; U; CPU iOS 18_2_1 like Mac OS X;)", "hl": "en", "gl": "US"}},
        {"client": {"clientName": "TVHTML5_SIMPLY_EMBEDDED_PLAYER", "clientVersion": "2.0", "hl": "en", "gl": "US"}, "thirdParty": {"embedUrl": "https://www.youtube.com/"}}
    ]
    for ctx in clients:
        try:
            ua = ctx["client"].get("userAgent", UA)
            r = safe_req("POST", "https://www.youtube.com/youtubei/v1/player?prettyPrint=false", json={"videoId": vid, "context": ctx}, headers={"User-Agent": ua}, timeout=8)
            if r.status_code == 200:
                tracks = r.json().get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
                if tracks:
                    tr = next((x for x in tracks if x.get("languageCode", "").startswith("en")), tracks[0])
                    cu = tr.get("baseUrl")
                    if cu:
                        subs = parse_captions(safe_req("GET", cu + "&fmt=json3", headers={"User-Agent": ua}, timeout=8).text) or parse_captions(safe_req("GET", cu, headers={"User-Agent": ua}, timeout=8).text)
                        if subs: return subs
        except Exception: pass
    return []

def w_piped_inv(vid: str):
    for host in ["https://pipedapi.kavin.rocks", "https://api.piped.private.coffee", "https://pipedapi.leptons.xyz"]:
        try:
            r = safe_req("GET", f"{host}/streams/{vid}", timeout=8)
            if r.status_code == 200 and r.json().get("subtitles"):
                s_list = r.json()["subtitles"]
                ch = next((x for x in s_list if x.get("code", "").startswith("en")), s_list[0])
                if ch.get("url"):
                    subs = parse_captions(safe_req("GET", ch["url"], timeout=8).text)
                    if subs: return subs
        except Exception: pass
    for inv in ["https://inv.nadeko.net", "https://invidious.nerdvpn.de", "https://yewtu.be"]:
        try:
            r = safe_req("GET", f"{inv}/api/v1/captions/{vid}", timeout=8)
            if r.status_code == 200 and r.json().get("captions"):
                caps = r.json()["captions"]
                ch = next((x for x in caps if x.get("language_code", "").startswith("en")), caps[0])
                if ch.get("label"):
                    subs = parse_captions(safe_req("GET", f"{inv}/api/v1/captions/{vid}", params={"label": ch["label"]}, timeout=8).text)
                    if subs: return subs
        except Exception: pass
    return []

def w_proxy(vid: str, proxy_tpl: str):
    w_url = f"https://www.youtube.com/watch?v={vid}&hl=en"
    r1 = safe_req("GET", proxy_tpl.format(urllib.parse.quote(w_url, safe='')), timeout=10)
    if r1.status_code == 200 and '"captionTracks":' in r1.text:
        cu = extract_cap_url(r1.text)
        if cu:
            subs = parse_captions(safe_req("GET", proxy_tpl.format(urllib.parse.quote(cu + "&fmt=json3", safe='')), timeout=10).text)
            if not subs:
                subs = parse_captions(safe_req("GET", proxy_tpl.format(urllib.parse.quote(cu, safe='')), timeout=10).text)
            return subs
    return []

def w_ytapi(vid: str):
    langs = ['en', 'en-US', 'en-GB', 'a.en']
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        return YouTubeTranscriptApi.get_transcript(vid, languages=langs)
    fetched = YouTubeTranscriptApi().fetch(vid, languages=langs)
    return [{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched]

def fetch_youtube_subtitles(video_id: str) -> list[dict]:
    tasks = [
        lambda: w_tactiq(video_id),
        lambda: w_notegpt(video_id),
        lambda: w_yttrans(video_id),
        lambda: w_innertube(video_id),
        lambda: w_piped_inv(video_id),
        lambda: w_proxy(video_id, "https://api.codetabs.com/v1/proxy?quest={}"),
        lambda: w_proxy(video_id, "https://api.allorigins.win/raw?url={}"),
        lambda: w_ytapi(video_id)
    ]
    ex = ThreadPoolExecutor(max_workers=8)
    try:
        futures = [ex.submit(fn) for fn in tasks]
        for fut in as_completed(futures, timeout=15):
            try:
                res = fut.result()
                if res and len(res) > 0:
                    ex.shutdown(wait=False, cancel_futures=True)
                    return res
            except Exception:
                pass
    except Exception:
        pass
    finally:
        ex.shutdown(wait=False)
    raise RuntimeError("NEED_CLIENT_FETCH")

def clean_fillers(t: str) -> str:
    c = re.sub(r"\s+", " ", re.sub(r"\b(uh+|um+|er+|erm+|ah+|hmm+)\b", "", t, flags=re.I)).strip()
    return c if c else t.strip()

def raw_gtrans(block: str) -> str:
    if not block or not block.strip(): return block
    for url, client in [("https://translate.googleapis.com/translate_a/single", "gtx"), ("https://clients5.google.com/translate_a/t", "dict-chrome-ex")]:
        try:
            r = safe_req("POST", url, params={"client": client, "sl": "en", "tl": "vi", "dt": "t"}, data={"q": block}, timeout=7)
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
    return out

def prepare_initial_subs(vid: str, raw_subs: list[dict]):
    subs = raw_subs[:3000]
    for s in subs: s["text"] = clean_txt(s.get("text", "")) or "..."
    init_n = min(80, len(subs))
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
        r = safe_req("GET", "https://translate.googleapis.com/translate_tts", params={"ie": "UTF-8", "q": c, "tl": "vi", "client": "tw-ob"}, timeout=6)
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
    if not req.subtitles: return {"error": "Video này không có phụ đề tiếng Anh."}
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

HTML_CONTENT = r"""<!DOCTYPE html>
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

    <div class="bg-slate-900 border border-slate-800 p-2.5 rounded-xl mb-3 space-y-2">
      <div class="flex gap-2">
        <div class="relative flex-1 min-w-0">
          <input id="videoUrl" type="text" placeholder="Dán mọi link YouTube (watch, live, youtu.be)..." class="w-full p-2.5 pr-8 rounded-lg bg-slate-950 border border-slate-700 text-white text-xs sm:text-sm">
          <button onclick="document.getElementById('videoUrl').value='';document.getElementById('videoUrl').focus();" class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white text-xs px-1">✕</button>
        </div>
        <button onclick="analyzeVideo()" id="btnLoad" class="bg-blue-600 hover:bg-blue-500 active:bg-blue-700 px-4 py-2.5 rounded-lg font-semibold text-xs sm:text-sm shrink-0">⚡ Dịch Video</button>
      </div>
      <div class="flex items-center gap-1.5 overflow-x-auto pb-0.5 text-[11px]">
        <span class="text-slate-400 shrink-0">🎯 Video mẫu:</span>
        <button onclick="quickLoad('gedoSfZvBgE')" class="bg-slate-800 hover:bg-slate-700 text-blue-300 px-2.5 py-1 rounded-md shrink-0">1. Polyp Đại Tràng</button>
        <button onclick="quickLoad('D_9Qzo_H7kk')" class="bg-slate-800 hover:bg-slate-700 text-emerald-300 px-2.5 py-1 rounded-md shrink-0">2. Hội Nghị Live (D_9Qzo_H7kk)</button>
      </div>
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
        <div id="subtitleList" class="flex-1 overflow-y-auto space-y-2 pr-1 relative">
          <p class="text-slate-400 text-xs sm:text-sm text-center pt-16">Đang khởi tạo trình phát...</p>
        </div>
      </div>
    </div>
  </div>

  <script src="https://www.youtube.com/iframe_api"></script>
  <script>
    let player, subtitles = [], activeTimer = null, activeSubIndex = 0, jobId = 0;
    let cabinEnabled = false, isSpeaking = false, lastSpokenIdx = -1, duckVol = 20, cabinRate = 1.2;
    const cabinAudio = new Audio(), dictAudio = new Audio();
    cabinAudio.playsInline = true; dictAudio.playsInline = true;

    function extractVid(url) {
      if (!url) return null;
      const u = url.trim();
      const m = u.match(/(?:v=|youtu\.be\/|embed\/|shorts\/|live\/|\/v\/)([0-9A-Za-z_-]{11})/);
      if (m) return m[1];
      const m2 = u.match(/(?:^|[^0-9A-Za-z_-])([0-9A-Za-z_-]{11})(?:$|[^0-9A-Za-z_-])/);
      return m2 ? m2[1] : null;
    }

    function quickLoad(vid) {
      const u = "https://www.youtube.com/watch?v=" + vid;
      document.getElementById("videoUrl").value = u;
      processUrlOrId(u);
    }

    function onYouTubeIframeAPIReady() {
      processUrlOrId("https://www.youtube.com/watch?v=gedoSfZvBgE");
    }

    function parseCapData(str) {
      const res = [];
      if (!str || !str.trim()) return res;
      if (str.trim().startsWith("{")) {
        try {
          const evs = JSON.parse(str).events || [];
          for (const ev of evs) {
            const txt = (ev.segs || []).map(s => s.utf8 || "").join("").split("\n").join(" ").trim();
            if (txt) res.push({text: txt, start: +(ev.tStartMs/1000).toFixed(2), duration: +((ev.dDurationMs||2500)/1000).toFixed(2)});
          }
          if (res.length) return res;
        } catch(e){}
      }
      const doc = new DOMParser().parseFromString(str, "text/xml");
      doc.querySelectorAll("text, p").forEach(n => {
        const txt = (n.textContent || "").split("\n").join(" ").trim();
        if (!txt) return;
        const st = n.hasAttribute("start") ? parseFloat(n.getAttribute("start")) : parseFloat(n.getAttribute("t")||0)/1000;
        const du = n.hasAttribute("dur") ? parseFloat(n.getAttribute("dur")) : parseFloat(n.getAttribute("d")||2500)/1000;
        res.push({text: txt, start: +st.toFixed(2), duration: +(du||2.5).toFixed(2)});
      });
      return res;
    }

    async function fetchFromPhoneBrowser(vid) {
      const wUrl = "https://www.youtube.com/watch?v=" + vid + "&hl=en";
      const proxies = [
        u => "https://api.codetabs.com/v1/proxy?quest=" + encodeURIComponent(u),
        u => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u)
      ];
      for (const px of proxies) {
        try {
          const r1 = await fetch(px(wUrl));
          const html = await r1.text();
          const m = html.match(/"captionTracks":\s*(\[.*?\])/);
          if (m) {
            const tracks = JSON.parse(m[1]);
            const tr = tracks.find(x => (x.languageCode || "").startsWith("en")) || tracks[0];
            if (tr && tr.baseUrl) {
              const r2 = await fetch(px(tr.baseUrl + "&fmt=json3"));
              let subs = parseCapData(await r2.text());
              if (!subs.length) {
                const r3 = await fetch(px(tr.baseUrl));
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
      const vid = extractVid(url);
      if (!vid) return alert("Link YouTube không hợp lệ!");

      initPlayer(vid);
      const box = document.getElementById("subtitleList");
      box.innerHTML = '<p class="text-blue-400 text-xs sm:text-sm text-center pt-16 animate-pulse">⏳ Đang trích xuất phụ đề & dịch song ngữ (3-8 giây)...</p>';
      document.getElementById("subCount").innerText = "Đang tải...";

      const r = await fetch("/api/process-video", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({url: "https://www.youtube.com/watch?v=" + vid})});
      let d = await r.json();

      if (d.need_client_fetch) {
        const rawSubs = await fetchFromPhoneBrowser(vid);
        if (rawSubs && rawSubs.length) {
          const r2 = await fetch("/api/process-raw-subtitles", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({video_id: vid, subtitles: rawSubs})});
          d = await r2.json();
        } else {
          d = {error: "Video này không có lời thoại/phụ đề (CC) trên YouTube. Hãy chọn một video có bật phụ đề (CC) nhé!"};
        }
      }

      if (d.error) {
        box.innerHTML = `<p class="text-amber-400 text-xs sm:text-sm text-center pt-12 px-4">${d.error}</p>`;
        document.getElementById("subCount").innerText = "0 câu";
      } else if (d.subtitles) {
        subtitles = d.subtitles;
        lastSpokenIdx = -1;
        renderSubs();
        startBgTrans();
      }
    }

    function initPlayer(vid) {
      if (player && player.loadVideoById) player.loadVideoById(vid);
      else if (window.YT && YT.Player) {
        player = new YT.Player('player', {height:'100%', width:'100%', videoId:vid, playerVars:{playsinline:1, rel:0}, events:{onStateChange: e => { if(e.data===2||e.data===0) stopCabin(); }}});
      }
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
        const words = s.text.split(" ").map(w => {
          const clean = w.replace(/[^a-zA-Z0-9-]/g, "");
          return `<span class="word-span" onclick="lookupWord(event,'${clean}',${i})">${w}</span>`;
        }).join(" ");
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
