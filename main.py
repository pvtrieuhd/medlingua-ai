import re, json, html, socket, base64, urllib.parse, urllib3, requests
import urllib3.util.connection as urllib3_conn
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi

urllib3_conn.allowed_gai_family = lambda: socket.AF_INET
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
ANDROID_UA = "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip"
WEB_CLIENT_VERSION = "2.20260115.01.00"

class VideoRequest(BaseModel):
    url: str
    custom_title: str = ""
    source_lang: str = "en"
    target_lang: str = "vi"

class RawSubsRequest(BaseModel):
    video_id: str
    custom_title: str = ""
    target_lang: str = "vi"
    subtitles: list[dict]

class BatchTranslateRequest(BaseModel):
    texts: list[str]
    target_lang: str = "vi"

class WordRequest(BaseModel):
    word: str
    context_sentence: str
    context_vi: str = ""

def extract_youtube_id(url: str):
    if not url:
        return None
    u = url.strip()
    m = re.search(r"(?:v=|youtu\.be\/|embed\/|shorts\/|live\/|\/v\/)([0-9A-Za-z_-]{11})", u)
    if m:
        return m.group(1)
    m2 = re.search(r"(?:^|[^0-9A-Za-z_-])([0-9A-Za-z_-]{11})(?:$|[^0-9A-Za-z_-])", u)
    return m2.group(1) if m2 else None

def safe_req(method: str, url: str, **kw):
    kw.setdefault("timeout", 9)
    try:
        return requests.request(method, url, **kw)
    except Exception:
        kw["verify"] = False
        return requests.request(method, url, **kw)

def clean_txt(raw: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html.unescape(raw or "")).replace("\n", " ").strip()
    return re.sub(r"\s+", " ", t)

def to_sec(val) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val or "0").strip().replace(",", ".")
    if ":" in s:
        p = s.split(":")
        if len(p) == 3:
            return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
        if len(p) == 2:
            return int(p[0]) * 60 + float(p[1])
    try:
        return float(s)
    except Exception:
        return 0.0

def is_valid_subs(subs: list[dict]) -> bool:
    if not subs or len(subs) == 0:
        return False
    joined = " ".join(s.get("text", "").lower() for s in subs[:5])
    bad_phrases = [
        "blocking us from fetching",
        "generating a summary for you",
        "working on a fix",
        "subtitles are disabled",
        "transcript is not available"
    ]
    return not any(bp in joined for bp in bad_phrases)

def normalize_subs(res: list[dict]) -> list[dict]:
    if not res:
        return []
    cleaned = []
    for item in res:
        txt = item.get("text", "").strip()
        if not txt:
            continue
        if cleaned and cleaned[-1]["text"] == txt:
            continue
        cleaned.append({
            "text": txt,
            "start": round(float(item.get("start", 0)), 2),
            "duration": max(round(float(item.get("duration", 2.5)), 2), 1.2)
        })
    for i in range(len(cleaned) - 1):
        cur = cleaned[i]
        nxt = cleaned[i + 1]
        if cur["start"] + cur["duration"] > nxt["start"] and nxt["start"] > cur["start"]:
            cur["duration"] = max(round(nxt["start"] - cur["start"], 2), 1.0)
    return cleaned if is_valid_subs(cleaned) else []

def fetch_video_title(vid: str, custom_title: str = "") -> str:
    if custom_title and custom_title.strip():
        return custom_title.strip()
    for oembed in [
        f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={vid}",
        f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
    ]:
        try:
            r = safe_req("GET", oembed, timeout=4)
            if r.status_code == 200 and r.json().get("title"):
                return r.json()["title"]
        except Exception:
            pass
    return f"Video ({vid})"

def with_format(url: str, fmt: str = "json3") -> str:
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    query["fmt"] = fmt
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))

def _pb_str(field_no: int, val: str) -> bytes:
    b = val.encode("utf-8")
    tag = (field_no << 3) | 2
    length = len(b)
    len_bytes = bytearray()
    while length > 0x7F:
        len_bytes.append((length & 0x7F) | 0x80)
        length >>= 7
    len_bytes.append(length & 0x7F)
    return bytes([tag]) + bytes(len_bytes) + b

def build_get_transcript_params(video_id: str, kind: str = "asr", lang: str = "en") -> str:
    inner = _pb_str(1, kind) + _pb_str(2, lang) + _pb_str(3, "")
    inner_b64 = urllib.parse.quote(base64.b64encode(inner).decode("ascii"))
    outer = (
        _pb_str(1, video_id)
        + _pb_str(2, inner_b64)
        + bytes([(3 << 3) | 0, 1])
        + _pb_str(5, "engagement-panel-searchable-transcript-search-panel")
    )
    return base64.b64encode(outer).decode("ascii")

def parse_get_transcript_json(data) -> list[dict]:
    segments = []
    def _walk(node):
        if isinstance(node, dict):
            if "transcriptSegmentRenderer" in node:
                seg = node["transcriptSegmentRenderer"]
                st_ms = float(seg.get("startMs", 0))
                et_ms = float(seg.get("endMs", st_ms + 3000))
                snip = seg.get("snippet", {})
                txt = ""
                if "runs" in snip:
                    txt = "".join(r.get("text", "") for r in snip["runs"])
                elif "elementsAttributedString" in snip:
                    txt = snip["elementsAttributedString"].get("content", "")
                elif "simpleText" in snip:
                    txt = snip["simpleText"]
                txt = clean_txt(txt)
                if txt:
                    segments.append({
                        "text": txt,
                        "start": round(st_ms / 1000.0, 2),
                        "duration": max(round((et_ms - st_ms) / 1000.0, 2), 1.2)
                    })
            else:
                for v in node.values():
                    _walk(v)
        elif isinstance(node, list):
            for it in node:
                _walk(it)
    _walk(data)
    return normalize_subs(segments)

def parse_captions(content: str) -> list[dict]:
    res = []
    if not content or not content.strip():
        return res
    c = content.strip()
    if "WEBVTT" in c[:30] or ("-->" in c[:300] and "<text" not in c[:300]):
        for block in re.split(r"\n\s*\n", c.replace("\r\n", "\n")):
            lines = [x.strip() for x in block.split("\n") if x.strip()]
            t_idx = next((i for i, l in enumerate(lines) if "-->" in l), -1)
            if t_idx != -1:
                pts = lines[t_idx].split("-->")
                st, et = to_sec(pts[0]), to_sec(pts[1].split()[0])
                txt = clean_txt(" ".join(lines[t_idx+1:]))
                if txt:
                    res.append({"text": txt, "start": round(st, 2), "duration": max(round(et - st, 2), 1.5)})
        if res:
            return normalize_subs(res)
    if c.startswith("{"):
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict) and "contents" in parsed and isinstance(parsed["contents"], str):
                parsed = json.loads(parsed["contents"])
            if "actions" in parsed or "transcriptSegmentRenderer" in c:
                subs_tr = parse_get_transcript_json(parsed)
                if subs_tr:
                    return subs_tr
            for ev in (parsed.get("events") or []):
                if ev.get("aAppend") == 1:
                    continue
                txt = clean_txt("".join(s.get("utf8", "") for s in ev.get("segs", [])))
                if txt:
                    res.append({"text": txt, "start": round(float(ev.get("tStartMs", 0)) / 1000, 2), "duration": round(float(ev.get("dDurationMs", 2500)) / 1000, 2)})
            if res:
                return normalize_subs(res)
        except Exception:
            pass
    for m in re.finditer(r'<text\b([^>]*)>(.*?)</text>', c, re.DOTALL):
        attrs, inner = m.group(1), m.group(2)
        st_m = re.search(r'start="([\d\.]+)"', attrs)
        du_m = re.search(r'dur="([\d\.]+)"', attrs)
        txt = clean_txt(inner)
        if st_m and txt:
            res.append({"text": txt, "start": round(float(st_m.group(1)), 2), "duration": round(float(du_m.group(1)) if du_m else 2.5, 2)})
    if res:
        return normalize_subs(res)
    for m in re.finditer(r'<p\b([^>]*)>(.*?)</p>', c, re.DOTALL):
        attrs, inner = m.group(1), m.group(2)
        if 'a="1"' in attrs:
            continue
        st_m = re.search(r'\bt="(\d+)"', attrs)
        du_m = re.search(r'\bd="(\d+)"', attrs)
        txt = clean_txt(inner)
        if st_m and txt:
            res.append({"text": txt, "start": round(float(st_m.group(1)) / 1000, 2), "duration": round(float(du_m.group(1)) / 1000 if du_m else 2.5, 2)})
    return normalize_subs(res)

# ==============================================================================
# 7 LUỒNG TRÍCH XUẤT CHẠY SONG SONG (VƯỢT TƯỜNG LỬA CHO CẢ LOCAL & RENDER CLOUD)
# ==============================================================================

def w_android_single_session(vid: str) -> list[dict]:
    clients = [
        {"clientName": "ANDROID", "clientVersion": "20.10.38", "hl": "en", "gl": "US"},
        {"clientName": "IOS", "clientVersion": "20.10.4", "hl": "en", "gl": "US"},
        {"clientName": "ANDROID_VR", "clientVersion": "1.61.48", "androidSdkVersion": 32, "hl": "en", "gl": "US"}
    ]
    for cl in clients:
        s = requests.Session()
        s.headers.update({"User-Agent": ANDROID_UA, "Accept-Language": "en-US,en;q=0.9"})
        try:
            r = s.post(
                "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
                json={"context": {"client": cl}, "videoId": vid},
                timeout=8
            )
            if r.status_code == 200:
                tracks = r.json().get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
                if tracks:
                    tr = next((x for x in tracks if x.get("languageCode", "").startswith("en") and x.get("kind") != "asr"), None)
                    if not tr:
                        tr = next((x for x in tracks if x.get("languageCode", "").startswith("en")), tracks[0])
                    base_url = tr.get("baseUrl", "").replace("\\u0026", "&").replace("\\/", "/")
                    if base_url and "exp=xpe" not in base_url:
                        json3_url = with_format(base_url, "json3")
                        r_sub = s.get(json3_url, timeout=8)
                        if r_sub.status_code == 200 and r_sub.text.strip():
                            subs = parse_captions(r_sub.text)
                            if subs:
                                return subs
                        r_xml = s.get(base_url.replace("&fmt=srv3", ""), timeout=8)
                        if r_xml.status_code == 200 and r_xml.text.strip():
                            subs = parse_captions(r_xml.text)
                            if subs:
                                return subs
        except Exception:
            pass
        finally:
            s.close()
    return []

def w_ytapi(vid: str) -> list[dict]:
    langs = ['en', 'en-US', 'en-GB', 'a.en', 'en-CA', 'en-AU', 'en-IN']
    try:
        ytt = YouTubeTranscriptApi()
        if hasattr(ytt, "fetch"):
            fetched = ytt.fetch(vid, languages=langs)
            subs = normalize_subs([{"text": clean_txt(s.text), "start": round(s.start, 2), "duration": round(s.duration, 2)} for s in fetched if clean_txt(s.text)])
            if subs:
                return subs
        if hasattr(ytt, "list"):
            t_list = ytt.list(vid)
            for tr in t_list:
                fetched = tr.fetch()
                subs = normalize_subs([{"text": clean_txt(s.text), "start": round(s.start, 2), "duration": round(s.duration, 2)} for s in fetched if clean_txt(s.text)])
                if subs:
                    return subs
    except Exception:
        pass
    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            raw = YouTubeTranscriptApi.get_transcript(vid, languages=langs)
            subs = normalize_subs([{"text": clean_txt(s["text"]), "start": round(s["start"], 2), "duration": round(s["duration"], 2)} for s in raw if clean_txt(s.get("text", ""))])
            if subs:
                return subs
    except Exception:
        pass
    return []

def w_tactiq_direct(vid: str) -> list[dict]:
    """Luồng độc lập 3: Gọi Tactiq (hiệu quả cao trên Cloud IP)"""
    for lc in ["en", "en-US", "en-GB"]:
        try:
            r = safe_req("POST", "https://tactiq-apps-prod.tactiq.io/transcript",
                         json={"videoUrl": f"https://www.youtube.com/watch?v={vid}", "langCode": lc},
                         headers={"Origin": "https://tactiq.io", "Referer": "https://tactiq.io/", "Content-Type": "application/json", "User-Agent": UA}, timeout=10)
            if r.status_code == 200 and r.json().get("captions"):
                subs = [{"text": clean_txt(c.get("text", "")), "start": round(to_sec(c.get("start", 0)), 2), "duration": round(to_sec(c.get("dur", 2.5)), 2)} for c in r.json()["captions"] if clean_txt(c.get("text", ""))]
                subs = normalize_subs(subs)
                if subs:
                    return subs
        except Exception:
            pass
    return []

def w_notegpt_direct(vid: str) -> list[dict]:
    """Luồng độc lập 4: Gọi NoteGPT trực tiếp & qua Proxy"""
    urls = [
        f"https://notegpt.io/api/v2/video-transcript?platform=youtube&video_id={vid}",
        f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(f'https://notegpt.io/api/v2/video-transcript?platform=youtube&video_id={vid}', safe='')}"
    ]
    for u in urls:
        try:
            r = safe_req("GET", u, headers={"Referer": "https://notegpt.io/youtube-transcript-generator", "User-Agent": UA}, timeout=10)
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
                        subs = normalize_subs(subs)
                        if subs:
                            return subs
        except Exception:
            pass
    return []

def w_google_translate_proxy(vid: str) -> list[dict]:
    """Luồng độc lập 5: Đi xuyên qua Google Translate Web Proxy (Dùng IP nội bộ của Google để lấy trang Watch)"""
    gt_url = f"https://www-youtube-com.translate.goog/watch?v={vid}&hl=en&_x_tr_sl=es&_x_tr_tl=en&_x_tr_hl=en"
    try:
        r = safe_req("GET", gt_url, headers={"User-Agent": UA}, timeout=10)
        if r.status_code == 200 and '"captionTracks":' in r.text:
            urls = re.findall(r'"baseUrl"\s*:\s*"(https://www\.youtube\.com/api/timedtext[^"]+)"', r.text)
            for raw_u in urls:
                clean_u = raw_u.replace("\\u0026", "&").replace("\\/", "/").replace("&fmt=srv3", "")
                if "exp=xpe" not in clean_u:
                    r_sub = safe_req("GET", with_format(clean_u, "json3"), headers={"User-Agent": UA}, timeout=8)
                    subs = parse_captions(r_sub.text)
                    if subs:
                        return subs
    except Exception:
        pass
    return []

def w_get_transcript_panel(vid: str) -> list[dict]:
    """Luồng độc lập 6: Cổng /youtubei/v1/get_transcript trực tiếp & qua Proxy"""
    params_candidates = [
        build_get_transcript_params(vid, kind="asr", lang="en"),
        build_get_transcript_params(vid, kind="", lang="en")
    ]
    endpoints = [
        "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        "https://api.cors.lol/?url=" + urllib.parse.quote("https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false", safe="")
    ]
    for ep in endpoints:
        for p_val in params_candidates:
            try:
                client_ctx = {"clientName": "WEB", "clientVersion": WEB_CLIENT_VERSION, "hl": "en", "gl": "US"}
                r = safe_req("POST", ep, json={"context": {"client": client_ctx}, "params": p_val},
                             headers={"Origin": "https://www.youtube.com", "Content-Type": "application/json", "User-Agent": UA}, timeout=9)
                if r.status_code == 200:
                    subs = parse_get_transcript_json(r.json())
                    if subs:
                        return subs
            except Exception:
                pass
    return []

def w_invidious_piped(vid: str) -> list[dict]:
    """Luồng độc lập 7: Hệ thống máy chủ Piped & Invidious quốc tế"""
    for host in ["https://pipedapi.kavin.rocks", "https://api.piped.private.coffee", "https://pipedapi.tokhmi.xyz"]:
        try:
            r = safe_req("GET", f"{host}/streams/{vid}", timeout=7)
            if r.status_code == 200 and r.json().get("subtitles"):
                s_list = r.json()["subtitles"]
                ch = next((x for x in s_list if x.get("code", "").startswith("en")), s_list[0])
                if ch.get("url"):
                    subs = parse_captions(safe_req("GET", ch["url"], timeout=7).text)
                    if subs:
                        return subs
        except Exception:
            pass
    for inv in ["https://inv.nadeko.net", "https://invidious.nerdvpn.de", "https://yewtu.be"]:
        try:
            r = safe_req("GET", f"{inv}/api/v1/captions/{vid}", timeout=7)
            if r.status_code == 200 and r.json().get("captions"):
                caps = r.json()["captions"]
                ch = next((x for x in caps if x.get("language_code", "").startswith("en")), caps[0])
                if ch.get("label"):
                    subs = parse_captions(safe_req("GET", f"{inv}/api/v1/captions/{vid}", params={"label": ch["label"]}, timeout=7).text)
                    if subs:
                        return subs
        except Exception:
            pass
    return []

def fetch_youtube_subtitles(video_id: str) -> list[dict]:
    tasks = [
        lambda: w_android_single_session(video_id),
        lambda: w_ytapi(video_id),
        lambda: w_tactiq_direct(video_id),
        lambda: w_notegpt_direct(video_id),
        lambda: w_google_translate_proxy(video_id),
        lambda: w_get_transcript_panel(video_id),
        lambda: w_invidious_piped(video_id)
    ]
    ex = ThreadPoolExecutor(max_workers=7)
    try:
        futures = [ex.submit(fn) for fn in tasks]
        for fut in as_completed(futures, timeout=15):
            try:
                res = fut.result()
                if is_valid_subs(res):
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

def raw_gtrans(block: str, tl: str = "vi") -> str:
    if not block or not block.strip():
        return block
    for url, client in [("https://translate.googleapis.com/translate_a/single", "gtx"), ("https://clients5.google.com/translate_a/t", "dict-chrome-ex")]:
        try:
            r = safe_req("POST", url, params={"client": client, "sl": "auto", "tl": tl, "dt": "t"}, data={"q": block}, headers={"User-Agent": UA}, timeout=6)
            if r.status_code == 200:
                d = r.json()
                if client == "gtx" and d and d[0]:
                    return "".join([p[0] for p in d[0] if p and p[0]]).strip()
                elif isinstance(d, list) and d:
                    return (d[0] if isinstance(d[0], str) else d[0][0]).strip()
        except Exception:
            pass
    return block

def translate_single(t: str, tl: str = "vi") -> str:
    c = clean_fillers(t.strip())
    res = raw_gtrans(c, tl=tl)
    return res if res else t

def translate_batch_list(texts: list[str], batch_size: int = 35, tl: str = "vi") -> list[str]:
    out = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        lines = [f"[{idx}] {clean_fillers(x) + ('.' if clean_fillers(x)[-1:] not in '.?!' else '')}" for idx, x in enumerate(chunk)]
        trans_block = raw_gtrans("\n".join(lines), tl=tl)
        rmap = {int(k): v.strip().rstrip(".") for k, v in re.findall(r"\[\s*(\d+)\s*\]\s*(.*?)(?=\[\s*\d+\s*\]|$)", trans_block, re.DOTALL)}
        for idx, orig in enumerate(chunk):
            val = rmap.get(idx, "")
            out.append(val if val else translate_single(orig, tl=tl))
    return out

def prepare_initial_subs(vid: str, raw_subs: list[dict], title: str, target_lang: str = "vi"):
    subs = raw_subs[:3500]
    for s in subs:
        s["text"] = clean_txt(s.get("text", "")) or "..."
    init_n = min(20, len(subs))
    init_vi = translate_batch_list([subs[i]["text"] for i in range(init_n)], batch_size=20, tl=target_lang)
    for i, s in enumerate(subs):
        s["vi"] = init_vi[i] if i < init_n else "⏳ Đang dịch..."
        s["translated"] = (i < init_n)
    return {
        "video_id": vid,
        "title": title,
        "subtitles": subs
    }

@app.get("/api/tts")
async def tts(text: str = Query(..., max_length=500), tl: str = Query("vi")):
    c = text.replace("⏳", "").strip()[:195]
    if not c:
        return Response(status_code=204)
    try:
        r = safe_req("GET", "https://translate.googleapis.com/translate_tts", params={"ie": "UTF-8", "q": c, "tl": tl, "client": "tw-ob"}, headers={"User-Agent": UA}, timeout=6)
        if r.status_code == 200 and r.content:
            return Response(content=r.content, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})
    except Exception:
        pass
    return Response(status_code=502)

@app.post("/api/process-video")
async def process_video(req: VideoRequest):
    vid = extract_youtube_id(req.url)
    if not vid:
        return {"error": "Link YouTube không hợp lệ!"}
    title = fetch_video_title(vid, req.custom_title)
    try:
        subs = fetch_youtube_subtitles(vid)
        return prepare_initial_subs(vid, subs, title, req.target_lang)
    except Exception:
        return {
            "need_client_fetch": True,
            "video_id": vid,
            "title": title,
            "pb_asr": build_get_transcript_params(vid, "asr", "en"),
            "pb_man": build_get_transcript_params(vid, "", "en")
        }

@app.post("/api/process-raw-subtitles")
async def process_raw_subtitles(req: RawSubsRequest):
    title = fetch_video_title(req.video_id, req.custom_title)
    if is_valid_subs(req.subtitles):
        return prepare_initial_subs(req.video_id, req.subtitles, title, req.target_lang)
    return {
        "error": "Video này hiện bị khóa truy cập phụ đề từ máy chủ. Bạn hãy bấm nút '🎙️ AI Nghe Loa' để nghe và dịch trực tiếp theo thời gian thực nhé!"
    }

@app.post("/api/translate-batch")
async def api_translate_batch(req: BatchTranslateRequest):
    return {"translations": translate_batch_list(req.texts, batch_size=35, tl=req.target_lang)}

@app.post("/api/lookup-word")
async def lookup_word(req: WordRequest):
    w = req.word.strip()
    low = w.lower()
    cands = [low]
    for base in list(cands):
        if base.endswith("ies"):
            cands.append(base[:-3] + "y")
        if base.endswith("es"):
            cands.append(base[:-2])
        if base.endswith("s") and not base.endswith("ss"):
            cands.append(base[:-1])
        if base.endswith("ed"):
            cands.extend([base[:-2], base[:-1]])
        if base.endswith("ing"):
            cands.extend([base[:-3], base[:-3] + "e"])

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
        except Exception:
            pass

    vi_w = translate_single(w)
    vi_sen = req.context_vi if (req.context_vi and "⏳" not in req.context_vi) else translate_single(req.context_sentence)
    exp = (f"• Định nghĩa: {en_def}\n" if en_def else "") + f"• Dịch câu: \"{vi_sen}\""
    return {"word": w, "ipa": ipa, "pos": pos, "meaning": vi_w, "explanation": exp, "audio": audio}

HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>MedLingua AI Studio - Học Tiếng Anh Qua Video Thời Gian Thực</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    .word-span { cursor: pointer; padding: 1px 4px; border-radius: 4px; display: inline-block; transition: all 0.12s; }
    .word-span:hover, .word-span:active { background: #fde047; color: #000; font-weight: 600; }
    .active-sub { background: #1e3a8a !important; border-left: 4px solid #38bdf8 !important; }
    .cabin-glow { box-shadow: 0 0 15px rgba(16,185,129,0.35); border-color: #10b981 !important; }
    .tab-btn { transition: all 0.15s; }
    .tab-active { background: #dc2626 !important; color: #fff !important; font-weight: 700; box-shadow: 0 2px 8px rgba(220,38,38,0.4); }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0f172a; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen pb-8">
  <div class="max-w-7xl mx-auto px-2 sm:px-4 pt-3">
    <!-- HEADER -->
    <header class="border-b border-slate-800 pb-3 mb-4 flex flex-wrap justify-between items-center gap-2">
      <div class="flex items-center gap-2">
        <span class="text-2xl">🎬</span>
        <div>
          <h1 class="text-lg sm:text-xl font-bold text-blue-400 flex items-center gap-2 flex-wrap">
            MedLingua AI Studio
            <span class="text-[11px] bg-emerald-500/20 text-emerald-400 px-2.5 py-0.5 rounded-full font-medium">Real-Time Speech • Dịch Cabin</span>
          </h1>
          <p class="text-xs text-slate-400">Dán link YouTube bất kỳ • Tự động trích xuất phụ đề hoặc AI Nghe Loa dịch tức thì theo thời gian thực</p>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button onclick="openSavedModal()" class="bg-slate-900 hover:bg-slate-800 text-teal-400 border border-teal-500/40 px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5">
          📂 Xem các bản dịch đã lưu (<span id="savedCountBadge">0</span>)
        </button>
      </div>
    </header>

    <!-- KHUNG NẠP BÀI GIẢNG & ĐIỀU KHIỂN CHÍNH -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-4 mb-4">
      <!-- Cột trái: Nạp Video -->
      <div class="lg:col-span-4 bg-slate-900 border border-slate-800 p-4 rounded-xl space-y-3 shadow-lg">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2">
          <div class="flex items-center gap-2 text-red-400 font-bold text-sm">
            <span>▶️</span>
            <span>Nạp Video Bài Giảng</span>
          </div>
          <span class="text-[10px] bg-blue-600/30 text-blue-300 px-2 py-0.5 rounded font-semibold">Real-Time Engine</span>
        </div>

        <div>
          <label class="block text-xs text-slate-300 font-medium mb-1">Tiêu đề bài giảng (bỏ trống để tự lấy tên từ YouTube):</label>
          <input id="customTitle" type="text" placeholder="Nhập tên bài giảng (tùy chọn)..." class="w-full p-2.5 rounded-lg bg-slate-950 border border-slate-700 text-white text-xs focus:border-blue-500 focus:outline-none">
        </div>

        <div>
          <label class="block text-xs text-slate-300 font-medium mb-1">YouTube URL:</label>
          <div class="relative">
            <input id="videoUrl" type="text" placeholder="Dán link YouTube vào đây..." class="w-full p-2.5 pr-8 rounded-lg bg-slate-950 border border-slate-700 text-white text-xs focus:border-blue-500 focus:outline-none">
            <button onclick="document.getElementById('videoUrl').value='';document.getElementById('videoUrl').focus();" class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white text-xs px-1">✕</button>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">Ngôn ngữ bài giảng</label>
            <select id="sourceLang" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200">
              <option value="en-US" selected>Tiếng Anh (English)</option>
              <option value="en-GB">Tiếng Anh (UK)</option>
            </select>
          </div>
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">Ngôn ngữ dịch</label>
            <select id="targetLang" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-emerald-400 font-semibold">
              <option value="vi" selected>Tiếng Việt</option>
              <option value="en">Tiếng Anh</option>
            </select>
          </div>
        </div>

        <div class="flex gap-2 pt-1">
          <button onclick="analyzeVideo()" id="btnLoad" class="flex-1 bg-red-600 hover:bg-red-500 active:bg-red-700 text-white py-2.5 px-3 rounded-lg font-bold text-xs sm:text-sm transition flex items-center justify-center gap-1.5 shadow-lg">
            🚀 Phân Tích & Dịch Phụ Đề
          </button>
        </div>

        <div class="grid grid-cols-3 gap-1.5 pt-1">
          <button onclick="toggleLiveMicTranscribe()" id="btnLiveMic" class="bg-slate-800 hover:bg-slate-700 text-amber-300 border border-amber-500/30 py-2.5 px-2 rounded-lg font-semibold text-[11px] flex items-center justify-center gap-1">
            🎙️ AI Nghe Loa
          </button>
          <button onclick="openPasteModal()" class="bg-slate-800 hover:bg-slate-700 text-purple-300 border border-purple-500/30 py-2.5 px-2 rounded-lg font-semibold text-[11px] flex items-center justify-center gap-1">
            📋 Dán Transcript
          </button>
          <label class="cursor-pointer bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 py-2.5 px-2 rounded-lg font-semibold text-[11px] flex items-center justify-center gap-1">
            ☁️ Nạp SRT/VTT
            <input type="file" accept=".srt,.vtt,.txt" class="hidden" onchange="handleSrtUpload(event)">
          </label>
        </div>
      </div>

      <!-- Cột phải: Tiêu đề bài giảng, Thanh 7 Tab & Dịch Cabin -->
      <div class="lg:col-span-8 bg-slate-900 border border-slate-800 p-4 rounded-xl flex flex-col justify-between shadow-lg">
        <div>
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3 mb-3">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <h2 id="lectureTitleDisplay" class="text-base sm:text-lg font-bold text-white truncate">Chưa nạp video</h2>
                <button onclick="analyzeVideo()" class="bg-slate-800 hover:bg-slate-700 text-xs text-blue-300 px-2.5 py-1 rounded-md font-semibold shrink-0">🔄 Tải lại</button>
                <button onclick="saveCurrentLecture()" class="bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 text-xs px-2.5 py-1 rounded-md font-semibold shrink-0">💾 Lưu bản dịch</button>
              </div>
              <p id="lectureUrlDisplay" class="text-xs text-slate-400 font-mono mt-0.5 truncate">Hãy dán link YouTube vào ô bên trái để bắt đầu...</p>
            </div>
            <span id="subCount" class="text-xs bg-slate-800 text-emerald-400 px-2.5 py-1 rounded-md font-semibold shrink-0">0 câu</span>
          </div>

          <!-- THANH 7 TAB CHỨC NĂNG -->
          <div class="bg-slate-950 p-1.5 rounded-xl border border-slate-800 flex flex-wrap gap-1.5 mb-3">
            <button onclick="switchTab('scene')" id="tab-btn-scene" class="tab-btn tab-active px-3 py-1.5 rounded-lg text-xs bg-slate-900 text-slate-300 flex items-center gap-1">
              📺 Phân Cảnh & Dịch
            </button>
            <button onclick="switchTab('wordbyword')" id="tab-btn-wordbyword" class="tab-btn px-3 py-1.5 rounded-lg text-xs bg-slate-900 text-slate-300 flex items-center gap-1">
              🔤 Dịch Từng Chữ
            </button>
            <button onclick="switchTab('vocab')" id="tab-btn-vocab" class="tab-btn px-3 py-1.5 rounded-lg text-xs bg-slate-900 text-slate-300 flex items-center gap-1">
              📚 Sổ Từ Vựng Video (<span id="vocabTabCount">0</span>)
            </button>
            <button onclick="switchTab('summary')" id="tab-btn-summary" class="tab-btn px-3 py-1.5 rounded-lg text-xs bg-slate-900 text-slate-300 flex items-center gap-1">
              ⚡ Tóm Tắt Nhanh
            </button>
            <button onclick="switchTab('clinical')" id="tab-btn-clinical" class="tab-btn px-3 py-1.5 rounded-lg text-xs bg-slate-900 text-slate-300 flex items-center gap-1">
              🏥 Lâm Sàng
            </button>
            <button onclick="switchTab('research')" id="tab-btn-research" class="tab-btn px-3 py-1.5 rounded-lg text-xs bg-slate-900 text-slate-300 flex items-center gap-1">
              🔬 Nghiên Cứu
            </button>
            <button onclick="switchTab('quiz')" id="tab-btn-quiz" class="tab-btn px-3 py-1.5 rounded-lg text-xs bg-slate-900 text-slate-300 flex items-center gap-1">
              📝 Ôn Tập Trắc Nghiệm
            </button>
          </div>
        </div>

        <!-- THANH ĐIỀU KHIỂN DỊCH CABIN SONG SONG -->
        <div id="cabinPanel" class="p-3 rounded-xl bg-slate-950 border border-slate-800">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <button id="btnCabinToggle" onclick="toggleCabinMode()" class="w-full sm:w-auto justify-center bg-slate-800 text-slate-200 border border-slate-700 px-3.5 py-2 rounded-xl font-bold text-xs sm:text-sm flex items-center gap-2">
              <span id="cabinDot" class="w-2.5 h-2.5 rounded-full bg-slate-500"></span>
              <span id="cabinBtnText">🎧 Bật Dịch Cabin (Nghe Thuyết Minh Song Song)</span>
            </button>
            <div class="flex items-center justify-between w-full sm:w-auto gap-2 text-xs">
              <div class="flex items-center gap-1.5 bg-slate-900 px-2.5 py-1.5 rounded-lg border border-slate-800">
                <span>🔊 Âm gốc:</span>
                <input type="range" min="5" max="70" value="20" oninput="updateOrigVol(this.value)" class="w-16 accent-blue-500">
                <span id="origVolValue" class="text-blue-400 font-bold">20%</span>
              </div>
              <select onchange="cabinRate=parseFloat(this.value);cabinAudio.playbackRate=cabinRate;" class="bg-slate-900 text-emerald-400 font-bold px-2 py-1.5 rounded-lg border border-slate-800">
                <option value="1.05">1.0x</option><option value="1.2" selected>1.2x</option><option value="1.35">1.35x</option>
              </select>
            </div>
          </div>
          <div id="cabinLiveBar" class="hidden mt-2 pt-2 border-t border-slate-800 flex items-center gap-2 text-xs">
            <span class="bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded font-semibold shrink-0">🎙️ Cabin</span>
            <p id="cabinLiveText" class="text-emerald-400 truncate">...</p>
          </div>
        </div>
      </div>
    </div>

    <!-- KHU VỰC HIỂN THỊ VIDEO & CÁC TAB CHỨC NĂNG -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
      <!-- Khung Trình phát Video YouTube -->
      <div class="lg:col-span-7 space-y-3">
        <div class="aspect-video bg-black rounded-xl overflow-hidden border border-slate-800 shadow-2xl">
          <div id="player" class="w-full h-full flex items-center justify-center text-slate-500 text-xs">
            Hãy dán link YouTube và bấm "🚀 Phân Tích & Dịch Phụ Đề"
          </div>
        </div>

        <!-- Hộp Tra Từ Điển Nhanh -->
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
            <button id="btnDictAudio" onclick="dictAudio.play()" class="hidden bg-emerald-600/20 text-emerald-300 px-3 py-2 rounded-lg text-xs font-semibold">🎙️ Phát âm chuẩn MP3</button>
            <button onclick="playCurrentSentence()" class="bg-blue-600 text-white px-4 py-2 rounded-lg text-xs font-semibold">🔊 Nghe câu này trong Video</button>
          </div>
        </div>
      </div>

      <!-- Khung Nội Dung Đa Tab bên phải -->
      <div class="lg:col-span-5 bg-slate-900 rounded-xl border border-slate-800 p-3.5 h-[62vh] lg:h-[560px] flex flex-col shadow-xl">
        <!-- TAB 1: PHÂN CẢNH & DỊCH -->
        <div id="tab-content-scene" class="flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2 flex justify-between items-center gap-2">
            <span class="font-bold text-xs sm:text-sm text-slate-200">📺 PHỤ ĐỀ SONG NGỮ TƯƠNG TÁC</span>
            <div class="flex items-center gap-1.5">
              <button onclick="clearAllSubtitles()" title="Xóa trắng danh sách phụ đề hiện tại" class="bg-slate-800 hover:bg-rose-600/30 text-slate-300 hover:text-rose-300 px-2 py-1 rounded text-[11px]">🗑️ Xóa</button>
              <input id="searchSubInput" type="text" oninput="filterSubtitles(this.value)" placeholder="🔍 Tìm câu..." class="bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-white w-32 focus:outline-none focus:border-blue-500">
            </div>
          </div>

          <!-- THANH TRẠNG THÁI THU ÂM & DỊCH TỨC THÌ (SIÊU TỐC) -->
          <div id="liveSpeechStatusBox" class="hidden mb-2 p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/40 space-y-1">
            <div class="flex items-center justify-between text-[11px]">
              <span class="font-bold text-amber-300 flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-red-500 animate-ping"></span>
                AI Đang Nghe & Dịch Thời Gian Thực:
              </span>
              <button onclick="forceCutCurrentStream()" class="bg-amber-600 hover:bg-amber-500 text-white px-2 py-0.5 rounded text-[10px] font-bold">⚡ Chốt câu ngay</button>
            </div>
            <p id="liveInterimText" class="text-xs text-white font-medium min-h-[18px]">Đang lắng nghe âm thanh từ video...</p>
            <p id="liveInterimVi" class="text-xs text-emerald-400 font-semibold min-h-[18px]"></p>
          </div>

          <div id="subtitleList" class="flex-1 overflow-y-auto space-y-2 pr-1 relative">
            <p class="text-slate-400 text-xs sm:text-sm text-center pt-16">Hãy dán link YouTube và bấm "🚀 Phân Tích & Dịch Phụ Đề" hoặc bật "🎙️ AI Nghe Loa".</p>
          </div>
        </div>

        <!-- TAB 2: DỊCH TỪNG CHỮ -->
        <div id="tab-content-wordbyword" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2">
            <h3 class="font-bold text-xs sm:text-sm text-blue-400">🔤 PHÂN TÍCH & DỊCH TỪNG CHỮ TRONG CÂU</h3>
            <p class="text-[11px] text-slate-400">Bấm vào từng từ bất kỳ để xem phiên âm và nghĩa tiếng Việt</p>
          </div>
          <div id="wordByWordList" class="flex-1 overflow-y-auto space-y-3 pr-1"></div>
        </div>

        <!-- TAB 3: SỔ TỪ VỰNG VIDEO -->
        <div id="tab-content-vocab" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2 flex justify-between items-center">
            <div>
              <h3 class="font-bold text-xs sm:text-sm text-emerald-400">📚 SỔ TỪ VỰNG TRÍCH XUẤT TỪ VIDEO</h3>
              <p class="text-[11px] text-slate-400">Tự động lọc thuật ngữ chuyên ngành thực tế trong bài giảng</p>
            </div>
            <button onclick="exportVocabTxt()" class="bg-slate-800 hover:bg-slate-700 text-xs text-teal-300 px-2.5 py-1 rounded-lg font-semibold">⬇️ Tải (.txt)</button>
          </div>
          <div id="vocabListContainer" class="flex-1 overflow-y-auto space-y-2 pr-1"></div>
        </div>

        <!-- TAB 4: TÓM TẮT NHANH -->
        <div id="tab-content-summary" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2">
            <h3 class="font-bold text-xs sm:text-sm text-amber-400">⚡ TÓM TẮT NHANH NỘI DUNG BÀI GIẢNG</h3>
            <p class="text-[11px] text-slate-400">Tổng hợp từ phụ đề thực tế của video đang mở</p>
          </div>
          <div id="summaryContainer" class="flex-1 overflow-y-auto space-y-3 pr-1 text-xs sm:text-sm"></div>
        </div>

        <!-- TAB 5: LÂM SÀNG -->
        <div id="tab-content-clinical" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2">
            <h3 class="font-bold text-xs sm:text-sm text-rose-400">🏥 ĐIỂM CỐT LÕI LÂM SÀNG (CLINICAL PEARLS)</h3>
            <p class="text-[11px] text-slate-400">Các câu chứa kỹ thuật khám, chẩn đoán và xử trí trong bài</p>
          </div>
          <div id="clinicalContainer" class="flex-1 overflow-y-auto space-y-2.5 pr-1"></div>
        </div>

        <!-- TAB 6: NGHIÊN CỨU -->
        <div id="tab-content-research" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2">
            <h3 class="font-bold text-xs sm:text-sm text-purple-400">🔬 DỮ LIỆU NGHIÊN CỨU & SỐ LIỆU</h3>
            <p class="text-[11px] text-slate-400">Các câu có số liệu thống kê, thử nghiệm và khuyến cáo trong bài</p>
          </div>
          <div id="researchContainer" class="flex-1 overflow-y-auto space-y-2.5 pr-1"></div>
        </div>

        <!-- TAB 7: ÔN TẬP TRẮC NGHIỆM -->
        <div id="tab-content-quiz" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2 flex justify-between items-center">
            <div>
              <h3 class="font-bold text-xs sm:text-sm text-yellow-400">📝 ÔN TẬP TRẮC NGHIỆM TỪ VIDEO</h3>
              <p class="text-[11px] text-slate-400">Câu hỏi tạo trực tiếp từ các câu thoại thực tế của video</p>
            </div>
            <button onclick="generateQuiz()" class="bg-blue-600 hover:bg-blue-500 text-white text-xs px-2.5 py-1 rounded-lg font-semibold">🔄 Tạo đề mới</button>
          </div>
          <div id="quizContainer" class="flex-1 overflow-y-auto space-y-3 pr-1"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- MODAL DÁN NHANH TRANSCRIPT -->
  <div id="pasteModal" class="hidden fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-2xl w-full p-5 flex flex-col shadow-2xl space-y-3">
      <div class="flex justify-between items-center border-b border-slate-800 pb-2">
        <h3 class="font-bold text-base text-purple-400">📋 Bản Chép Lời (Transcript) Của Video</h3>
        <button onclick="closePasteModal()" class="bg-slate-800 hover:bg-slate-700 w-8 h-8 rounded-full font-bold">✕</button>
      </div>
      <p class="text-xs text-slate-300 leading-relaxed">
        • Mọi câu thu được từ <b>🎙️ AI Nghe Loa</b> đều tự động ghi vào đây theo thời gian thực.<br>
        • Bạn cũng có thể dán trực tiếp bản chép lời (Show transcript) từ YouTube vào đây để dịch ngay:
      </p>
      <textarea id="pasteRawArea" rows="8" placeholder="Dán nội dung transcript vào đây..." class="w-full p-3 rounded-xl bg-slate-950 border border-slate-700 text-xs text-white font-mono focus:outline-none focus:border-purple-500"></textarea>
      <div class="flex justify-end gap-2">
        <button onclick="closePasteModal()" class="bg-slate-800 px-4 py-2 rounded-lg text-xs font-semibold">Đóng</button>
        <button onclick="submitPastedTranscript()" class="bg-purple-600 hover:bg-purple-500 text-white px-5 py-2 rounded-lg text-xs font-bold">⚡ Nạp & Dịch Ngay</button>
      </div>
    </div>
  </div>

  <!-- MODAL XEM CÁC BẢN DỊCH ĐÃ LƯU -->
  <div id="savedModal" class="hidden fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-2xl w-full p-5 max-h-[80vh] flex flex-col shadow-2xl">
      <div class="flex justify-between items-center border-b border-slate-800 pb-3 mb-3">
        <h3 class="font-bold text-base text-teal-400">📂 Danh Sách Các Bản Dịch Đã Lưu</h3>
        <button onclick="closeSavedModal()" class="bg-slate-800 hover:bg-slate-700 w-8 h-8 rounded-full font-bold">✕</button>
      </div>
      <div id="savedListBox" class="flex-1 overflow-y-auto space-y-2 pr-1"></div>
    </div>
  </div>

  <script src="https://www.youtube.com/iframe_api"></script>
  <script>
    let player = null, subtitles = [], currentVideoId = "", currentTitle = "";
    let activeTimer = null, activeSubIndex = 0, jobId = 0, currentTab = "scene";
    let cabinEnabled = false, isSpeaking = false, lastSpokenIdx = -1, duckVol = 20, cabinRate = 1.2;

    let speechRec = null, isLiveListening = false;
    let streamResultIndex = -1, streamConsumedWords = 0, streamChunkStartSec = 0;
    let liveUnreadWords = [], interimTransTimer = null;
    let extractedVocabList = [];

    const cabinAudio = new Audio(), dictAudio = new Audio();
    cabinAudio.playsInline = true; dictAudio.playsInline = true;

    // Bộ lọc loại bỏ các từ tiếng Anh thông dụng để Sổ Từ Vựng chỉ lấy thuật ngữ chuyên môn
    const STOP_WORDS = new Set([
      "the","and","that","this","with","from","have","will","your","they","what","when","where","which","there","their",
      "about","would","could","should","because","these","those","into","more","some","very","just","also","than","then",
      "them","were","been","being","here","going","think","know","look","make","like","time","year","years","people","other",
      "theyre","within","before","question","questions","podcast","welcome","episode","everybody","everyone","something",
      "nothing","everything","through","between","without","however","already","always","really","actually","probably",
      "different","important","example","another","around","during","number","second","little","things","course","talking",
      "talked","coming","started","starting","looking","trying","having","making","saying","seeing","taking","giving",
      "wanted","called","together","pretty","almost","either","whether","though","although","perhaps","maybe","basically"
    ]);

    function formatTime(sec) {
      const s = Math.max(0, Math.floor(sec || 0));
      const m = Math.floor(s / 60);
      const r = s % 60;
      return `${m}:${r < 10 ? '0' : ''}${r}`;
    }

    function extractVid(url) {
      if (!url) return null;
      const u = url.trim();
      const m = u.match(/(?:v=|youtu\.be\/|embed\/|shorts\/|live\/|\/v\/)([0-9A-Za-z_-]{11})/);
      if (m) return m[1];
      const m2 = u.match(/(?:^|[^0-9A-Za-z_-])([0-9A-Za-z_-]{11})(?:$|[^0-9A-Za-z_-])/);
      return m2 ? m2[1] : null;
    }

    async function fastTranslateClient(text, tl = "vi") {
      const clean = (text || "").trim();
      if (!clean) return "";
      try {
        const u = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=${tl}&dt=t&q=${encodeURIComponent(clean)}`;
        const r = await fetch(u);
        if (r.ok) {
          const d = await r.json();
          if (d && d[0]) {
            return d[0].map(p => p[0] || "").join("").trim();
          }
        }
      } catch(e) {}
      try {
        const r2 = await fetch("/api/translate-batch", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({texts: [clean], target_lang: tl})
        });
        const d2 = await r2.json();
        if (d2.translations && d2.translations[0]) return d2.translations[0];
      } catch(e) {}
      return clean;
    }

    function clearAllSubtitles() {
      jobId++;
      subtitles = [];
      extractedVocabList = [];
      activeSubIndex = 0;
      lastSpokenIdx = -1;
      document.getElementById("subCount").innerText = "0 câu";
      document.getElementById("vocabTabCount").innerText = "0";
      document.getElementById("pasteRawArea").value = "";
      document.getElementById("subtitleList").innerHTML = '<p class="text-slate-400 text-xs text-center pt-12">Danh sách phụ đề đang trống.</p>';
      document.getElementById("wordByWordList").innerHTML = "";
      document.getElementById("vocabListContainer").innerHTML = "";
      document.getElementById("summaryContainer").innerHTML = "";
      document.getElementById("clinicalContainer").innerHTML = "";
      document.getElementById("researchContainer").innerHTML = "";
      document.getElementById("quizContainer").innerHTML = "";
    }

    function switchTab(tabName) {
      currentTab = tabName;
      const tabs = ['scene', 'wordbyword', 'vocab', 'summary', 'clinical', 'research', 'quiz'];
      tabs.forEach(t => {
        document.getElementById('tab-content-' + t).classList.toggle('hidden', t !== tabName);
        document.getElementById('tab-btn-' + t).classList.toggle('tab-active', t === tabName);
      });
      if (tabName !== 'scene') {
        buildAllMedicalTabs();
      }
    }

    function onYouTubeIframeAPIReady() {
      updateSavedBadge();
    }

    function isValidSubsJS(arr) {
      if (!arr || !arr.length) return false;
      const joined = arr.slice(0, 5).map(x => (x.text || "").toLowerCase()).join(" ");
      return !(joined.includes("blocking us from fetching") || joined.includes("working on a fix"));
    }

    function normalizeSubsJS(arr) {
      const cleaned = [];
      for (const item of arr) {
        const txt = (item.text || "").replace(/\s+/g, " ").trim();
        if (!txt) continue;
        if (cleaned.length && cleaned[cleaned.length - 1].text === txt) continue;
        cleaned.push({text: txt, start: +(item.start || 0).toFixed(2), duration: Math.max(+(item.duration || 2.5).toFixed(2), 1.2)});
      }
      for (let i = 0; i < cleaned.length - 1; i++) {
        if (cleaned[i].start + cleaned[i].duration > cleaned[i+1].start && cleaned[i+1].start > cleaned[i].start) {
          cleaned[i].duration = Math.max(+(cleaned[i+1].start - cleaned[i].start).toFixed(2), 1.0);
        }
      }
      return isValidSubsJS(cleaned) ? cleaned : [];
    }

    function decodeHtmlEntities(str) {
      const txt = document.createElement("textarea");
      txt.innerHTML = str;
      return txt.value;
    }

    function toSecJS(val) {
      if (typeof val === "number") return val;
      const s = String(val || "0").trim().replace(",", ".");
      if (s.includes(":")) {
        const p = s.split(":");
        if (p.length === 3) return parseInt(p[0])*3600 + parseInt(p[1])*60 + parseFloat(p[2]);
        if (p.length === 2) return parseInt(p[0])*60 + parseFloat(p[1]);
      }
      return parseFloat(s) || 0;
    }

    function parseGetTranscriptTreeJS(root) {
      const segs = [];
      function walk(node) {
        if (!node || typeof node !== "object") return;
        if (Array.isArray(node)) {
          for (const it of node) walk(it);
          return;
        }
        if (node.transcriptSegmentRenderer) {
          const s = node.transcriptSegmentRenderer;
          const stMs = parseFloat(s.startMs || "0");
          const etMs = parseFloat(s.endMs || String(stMs + 3000));
          const snip = s.snippet || {};
          let txt = "";
          if (Array.isArray(snip.runs)) txt = snip.runs.map(r => r.text || "").join("");
          else if (snip.elementsAttributedString) txt = snip.elementsAttributedString.content || "";
          else if (snip.simpleText) txt = snip.simpleText;
          txt = decodeHtmlEntities(txt).replace(/\s+/g, " ").trim();
          if (txt) segs.push({text: txt, start: +(stMs/1000).toFixed(2), duration: Math.max(+((etMs - stMs)/1000).toFixed(2), 1.2)});
          return;
        }
        for (const k of Object.keys(node)) walk(node[k]);
      }
      walk(root);
      return normalizeSubsJS(segs);
    }

    function parseCapData(str) {
      const res = [];
      if (!str || !str.trim()) return res;
      const c = str.trim();
      if (c.includes("-->") && !c.slice(0, 300).includes("<text")) {
        const blocks = c.replace(/\r\n/g, "\n").split(/\n\s*\n/);
        for (const b of blocks) {
          const lines = b.split("\n").map(x => x.trim()).filter(Boolean);
          const idx = lines.findIndex(l => l.includes("-->"));
          if (idx !== -1) {
            const pts = lines[idx].split("-->");
            const st = toSecJS(pts[0]), et = toSecJS(pts[1].trim().split(/\s+/)[0]);
            const txt = decodeHtmlEntities(lines.slice(idx + 1).join(" ").replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();
            if (txt) res.push({text: txt, start: +st.toFixed(2), duration: Math.max(+(et - st).toFixed(2), 1.5)});
          }
        }
        if (res.length) return normalizeSubsJS(res);
      }
      if (c.startsWith("{")) {
        try {
          let parsed = JSON.parse(c);
          if (parsed.contents && typeof parsed.contents === "string") parsed = JSON.parse(parsed.contents);
          if (c.includes("transcriptSegmentRenderer")) {
            const trSegs = parseGetTranscriptTreeJS(parsed);
            if (trSegs.length) return trSegs;
          }
          const evs = parsed.events || [];
          for (const ev of evs) {
            if (ev.aAppend === 1) continue;
            const txt = (ev.segs || []).map(s => s.utf8 || "").join("").split("\n").join(" ").trim();
            if (txt) res.push({text: txt, start: +(ev.tStartMs/1000).toFixed(2), duration: +((ev.dDurationMs||2500)/1000).toFixed(2)});
          }
          if (res.length) return normalizeSubsJS(res);
        } catch(e){}
      }
      const textRegex = /<text\b([^>]*)>([\s\S]*?)<\/text>/g;
      let m;
      while ((m = textRegex.exec(c)) !== null) {
        const attrs = m[1], inner = m[2];
        const stM = attrs.match(/start="([\d\.]+)"/);
        const duM = attrs.match(/dur="([\d\.]+)"/);
        const clean = decodeHtmlEntities(inner.replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();
        if (stM && clean) res.push({text: clean, start: +parseFloat(stM[1]).toFixed(2), duration: +(duM ? parseFloat(duM[1]) : 2.5).toFixed(2)});
      }
      if (res.length) return normalizeSubsJS(res);
      const pRegex = /<p\b([^>]*)>([\s\S]*?)<\/p>/g;
      while ((m = pRegex.exec(c)) !== null) {
        const attrs = m[1], inner = m[2];
        if (attrs.includes('a="1"')) continue;
        const stM = attrs.match(/\bt="(\d+)"/);
        const duM = attrs.match(/\bd="(\d+)"/);
        const clean = decodeHtmlEntities(inner.replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();
        if (stM && clean) res.push({text: clean, start: +(parseFloat(stM[1])/1000).toFixed(2), duration: +(duM ? parseFloat(duM[1])/1000 : 2.5).toFixed(2)});
      }
      return normalizeSubsJS(res);
    }

    async function fetchFromPhoneBrowser(vid) {
      const tasks = [
        // 1. Nếu đang mở trên máy tính có chạy sẵn 127.0.0.1:8000 thì mượn luôn kết nối mạng nhà của 127.0.0.1
        (async () => {
          if (location.hostname === "127.0.0.1" || location.hostname === "localhost") throw new Error("already_local");
          const rLocal = await fetch("http://127.0.0.1:8000/api/process-video", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({url: "https://www.youtube.com/watch?v=" + vid})
          });
          if (!rLocal.ok) throw new Error("no_local");
          const dLocal = await rLocal.json();
          if (dLocal.subtitles && dLocal.subtitles.length) return dLocal.subtitles;
          throw new Error("empty_local");
        })(),
        // 2. Gọi Tactiq bọc qua CORS Proxy để không bị trình duyệt chặn CORS trên onrender.com
        (async () => {
          const rT = await fetch("https://api.cors.lol/?url=" + encodeURIComponent("https://tactiq-apps-prod.tactiq.io/transcript"), {
            method: "POST",
            headers: {"Content-Type": "text/plain;charset=UTF-8"},
            body: JSON.stringify({videoUrl: "https://www.youtube.com/watch?v=" + vid, langCode: "en"})
          });
          if (!rT.ok) throw new Error("tactiq_proxy");
          const dT = await rT.json();
          const subs = normalizeSubsJS((dT.captions || []).map(c => ({
            text: (c.text || "").replace(/\n/g, " ").trim(),
            start: +(parseFloat(c.start || 0)).toFixed(2),
            duration: +(parseFloat(c.dur || 2.5)).toFixed(2)
          })));
          if (!subs.length) throw new Error("empty");
          return subs;
        })(),
        // 3. Gọi NoteGPT qua codetabs / allorigins
        (async () => {
          const noteUrl = "https://notegpt.io/api/v2/video-transcript?platform=youtube&video_id=" + vid;
          for (const px of [
            u => "https://api.codetabs.com/v1/proxy?quest=" + encodeURIComponent(u),
            u => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u)
          ]) {
            try {
              const r = await fetch(px(noteUrl));
              if (r.ok) {
                const d = await r.json();
                const trs = ((d.data || {}).transcripts) || {};
                for (const k of Object.keys(trs)) {
                  const items = trs[k].custom || trs[k].default || trs[k].auto || [];
                  if (items.length) {
                    const subs = normalizeSubsJS(items.map(it => {
                      const st = toSecJS(it.start || 0);
                      const et = toSecJS(it.end || (st + 2.5));
                      return {text: it.text || "", start: st, duration: Math.max(et - st, 1.5)};
                    }));
                    if (subs.length) return subs;
                  }
                }
              }
            } catch(e) {}
          }
          throw new Error("notegpt_empty");
        })(),
        // 4. Gọi Piped / Invidious từ trình duyệt
        (async () => {
          for (const host of ["https://pipedapi.kavin.rocks", "https://api.piped.private.coffee"]) {
            try {
              const r = await fetch(`${host}/streams/${vid}`);
              if (r.ok) {
                const d = await r.json();
                if (d.subtitles && d.subtitles.length) {
                  const ch = d.subtitles.find(x => (x.code || "").startsWith("en")) || d.subtitles[0];
                  if (ch && ch.url) {
                    const r2 = await fetch(ch.url);
                    const subs = parseCapData(await r2.text());
                    if (subs.length) return subs;
                  }
                }
              }
            } catch(e) {}
          }
          throw new Error("piped_empty");
        })()
      ];
      try {
        return await Promise.any(tasks);
      } catch(e) {
        return [];
      }
    }

    function openPasteModal() {
      document.getElementById("pasteModal").classList.remove("hidden");
    }
    function closePasteModal() {
      document.getElementById("pasteModal").classList.add("hidden");
    }
    async function submitPastedTranscript() {
      const raw = document.getElementById("pasteRawArea").value.trim();
      if (!raw) return alert("Bạn hãy dán nội dung bản chép lời vào ô trống nhé!");
      let parsed = parseCapData(raw);
      if (!parsed.length) {
        const lines = raw.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
        let curTime = null, autoTime = 0;
        for (let i = 0; i < lines.length; i++) {
          const mTime = lines[i].match(/^(\d{1,2}:\d{2}(?::\d{2})?)\s*(.*)$/);
          if (mTime) {
            curTime = toSecJS(mTime[1]);
            if (mTime[2]) {
              parsed.push({text: mTime[2], start: +curTime.toFixed(2), duration: 4.5});
              autoTime = curTime + 4.5;
              curTime = null;
            }
          } else {
            const st = curTime !== null ? curTime : autoTime;
            parsed.push({text: lines[i], start: +st.toFixed(2), duration: 4.5});
            autoTime = st + 4.5;
            curTime = null;
          }
        }
        parsed = normalizeSubsJS(parsed);
      }
      if (!parsed.length) return alert("Không nhận diện được văn bản phụ đề!");
      closePasteModal();
      subtitles = parsed.map(s => ({...s, vi: "⏳ Đang dịch...", translated: false}));
      renderSubs();
      startBgTrans();
    }

    function createSubNode(s, i) {
      const div = document.createElement("div");
      div.id = "sub-" + i;
      div.className = "p-2.5 rounded-lg bg-slate-950/80 border border-slate-800 cursor-pointer hover:border-slate-700";
      const words = s.text.split(" ").map(w => {
        const clean = w.replace(/[^a-zA-Z0-9-]/g, "");
        return `<span class="word-span" onclick="lookupWord(event,'${clean}',${i})">${w}</span>`;
      }).join(" ");
      div.innerHTML = `
        <div class="flex justify-between items-start gap-2">
          <div class="text-xs text-slate-400 font-mono shrink-0 mt-1">${formatTime(s.start)}</div>
          <div class="text-sm sm:text-base font-medium leading-relaxed flex-1">${words}</div>
          <div class="flex gap-1 shrink-0">
            <button onclick="event.stopPropagation();playCabinOne(${i})" class="text-[11px] bg-emerald-600/20 text-emerald-300 px-2 py-1 rounded">🎙️ Cabin</button>
            <button onclick="event.stopPropagation();playSeg(${s.start},${s.duration},${i})" class="text-[11px] bg-slate-800 text-slate-300 px-2 py-1 rounded">🔊 Gốc</button>
          </div>
        </div>
        <div id="sub-vi-${i}" class="text-xs sm:text-sm text-emerald-400 font-medium mt-1 pl-9">${s.vi}</div>`;
      div.onclick = () => playSeg(s.start, s.duration, i);
      return div;
    }

    async function commitSpokenSegment(spokenEn, startSec) {
      const cleanEn = (spokenEn || "").replace(/\s+/g, " ").trim();
      if (!cleanEn) return;
      const box = document.getElementById("subtitleList");
      if (subtitles.length === 0) {
        box.innerHTML = "";
      }

      const newIdx = subtitles.length;
      const st = Math.max(0, +(startSec || 0).toFixed(2));
      const newSub = {text: cleanEn, start: st, duration: 4.0, vi: "⏳...", translated: false};
      subtitles.push(newSub);
      document.getElementById("subCount").innerText = `${subtitles.length} câu (Live)`;

      const area = document.getElementById("pasteRawArea");
      area.value = (area.value ? area.value + "\n" : "") + `${formatTime(st)} ${cleanEn}`;

      const node = createSubNode(newSub, newIdx);
      box.appendChild(node);
      box.scrollTo({top: box.scrollHeight, behavior: "smooth"});

      const tl = document.getElementById("targetLang").value || "vi";
      const viTxt = await fastTranslateClient(cleanEn, tl);
      subtitles[newIdx].vi = viTxt;
      subtitles[newIdx].translated = true;
      const el = document.getElementById("sub-vi-" + newIdx);
      if (el) el.innerText = viTxt;

      if (cabinEnabled) {
        speakText(viTxt);
      }
    }

    function forceCutCurrentStream() {
      if (liveUnreadWords.length > 0) {
        const chunk = liveUnreadWords.join(" ");
        streamConsumedWords += liveUnreadWords.length;
        liveUnreadWords = [];
        const st = streamChunkStartSec;
        if (player && player.getCurrentTime) streamChunkStartSec = Math.max(0, player.getCurrentTime() - 0.5);
        document.getElementById("liveInterimText").innerText = "Đang nghe tiếp...";
        document.getElementById("liveInterimVi").innerText = "";
        commitSpokenSegment(chunk, st);
      }
    }

    function toggleLiveMicTranscribe() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      const btn = document.getElementById("btnLiveMic");
      const statusBox = document.getElementById("liveSpeechStatusBox");
      if (!SR) {
        return alert("Trình duyệt này chưa hỗ trợ Web Speech API (Hãy mở bằng Chrome hoặc Edge).");
      }
      if (isLiveListening && speechRec) {
        forceCutCurrentStream();
        isLiveListening = false;
        speechRec.stop();
        statusBox.classList.add("hidden");
        btn.innerText = "🎙️ AI Nghe Loa";
        btn.className = "bg-slate-800 hover:bg-slate-700 text-amber-300 border border-amber-500/30 py-2.5 px-2 rounded-lg font-semibold text-[11px] flex items-center justify-center gap-1";
        return;
      }

      if (subtitles.length === 0) {
        document.getElementById("subtitleList").innerHTML = "";
      }

      speechRec = new SR();
      speechRec.lang = document.getElementById("sourceLang").value || "en-US";
      speechRec.continuous = true;
      speechRec.interimResults = true;
      isLiveListening = true;
      streamResultIndex = -1;
      streamConsumedWords = 0;
      liveUnreadWords = [];

      statusBox.classList.remove("hidden");
      btn.innerText = "🔴 Đang Nghe (Dừng)";
      btn.className = "bg-amber-600 text-white py-2.5 px-2 rounded-lg font-bold text-[11px] flex items-center justify-center gap-1 animate-pulse";

      if (player && player.playVideo) player.playVideo();

      speechRec.onresult = (event) => {
        const idx = event.resultIndex;
        if (idx !== streamResultIndex) {
          streamResultIndex = idx;
          streamConsumedWords = 0;
          streamChunkStartSec = (player && player.getCurrentTime) ? Math.max(0, player.getCurrentTime() - 1) : 0;
        }

        const fullTranscript = (event.results[idx][0].transcript || "").trim();
        const allWords = fullTranscript.split(/\s+/).filter(Boolean);
        const unread = allWords.slice(streamConsumedWords);
        liveUnreadWords = unread;

        while (liveUnreadWords.length >= 10) {
          const takeWords = liveUnreadWords.slice(0, 10);
          liveUnreadWords = liveUnreadWords.slice(10);
          streamConsumedWords += 10;
          const st = streamChunkStartSec;
          if (player && player.getCurrentTime) {
            streamChunkStartSec = Math.max(0, player.getCurrentTime() - 1);
          }
          commitSpokenSegment(takeWords.join(" "), st);
        }

        if (event.results[idx].isFinal) {
          if (liveUnreadWords.length > 0) {
            const rem = liveUnreadWords.join(" ");
            streamConsumedWords = allWords.length;
            liveUnreadWords = [];
            commitSpokenSegment(rem, streamChunkStartSec);
          }
          document.getElementById("liveInterimText").innerText = "🎙️ Đang nghe câu tiếp theo...";
          document.getElementById("liveInterimVi").innerText = "";
        } else if (liveUnreadWords.length > 0) {
          const previewEn = liveUnreadWords.join(" ");
          document.getElementById("liveInterimText").innerText = "🎙️ " + previewEn + "...";
          if (interimTransTimer) clearTimeout(interimTransTimer);
          interimTransTimer = setTimeout(async () => {
            if (liveUnreadWords.length > 0) {
              const tl = document.getElementById("targetLang").value || "vi";
              const viPre = await fastTranslateClient(liveUnreadWords.join(" "), tl);
              document.getElementById("liveInterimVi").innerText = "➔ " + viPre;
            }
          }, 350);
        }
      };

      speechRec.onend = () => {
        if (isLiveListening) {
          streamResultIndex = -1;
          streamConsumedWords = 0;
          try { speechRec.start(); } catch(e){}
        }
      };
      try { speechRec.start(); } catch(e){}
    }

    async function handleSrtUpload(event) {
      const file = event.target.files[0];
      if (!file) return;
      const text = await file.text();
      const parsed = parseCapData(text);
      if (!parsed.length) return alert("File phụ đề không đúng định dạng SRT/VTT!");
      clearAllSubtitles();
      currentTitle = document.getElementById("customTitle").value.trim() || file.name.replace(/\.[^.]+$/, "");
      document.getElementById("lectureTitleDisplay").innerText = currentTitle;
      subtitles = parsed.map(s => ({...s, vi: "⏳ Đang dịch...", translated: false}));
      renderSubs();
      startBgTrans();
    }

    function applyLoadedLecture(d) {
      currentVideoId = d.video_id;
      currentTitle = d.title || ("Video (" + d.video_id + ")");
      document.getElementById("lectureTitleDisplay").innerText = currentTitle;
      document.getElementById("lectureUrlDisplay").innerText = "https://www.youtube.com/watch?v=" + currentVideoId;
      subtitles = d.subtitles;
      lastSpokenIdx = -1;
      document.getElementById("pasteRawArea").value = subtitles.map(s => `${formatTime(s.start)} ${s.text}`).join("\n");
      renderSubs();
      startBgTrans();
    }

    async function processUrlOrId(url) {
      const vid = extractVid(url);
      if (!vid) return alert("Bạn hãy dán đường dẫn YouTube hợp lệ vào ô YouTube URL nhé!");

      clearAllSubtitles();
      currentVideoId = vid;
      const customT = document.getElementById("customTitle").value.trim();
      const targetLang = document.getElementById("targetLang").value;

      initPlayer(vid);
      document.getElementById("lectureTitleDisplay").innerText = "Đang tải video (" + vid + ")...";
      document.getElementById("lectureUrlDisplay").innerText = "https://www.youtube.com/watch?v=" + vid;
      const box = document.getElementById("subtitleList");
      box.innerHTML = `<p class="text-blue-400 text-xs sm:text-sm text-center pt-16 animate-pulse">⏳ Đang trích xuất phụ đề thực tế của video...</p>`;
      document.getElementById("subCount").innerText = "Đang tải...";

      const serverPromise = fetch("/api/process-video", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({url: "https://www.youtube.com/watch?v=" + vid, custom_title: customT, target_lang: targetLang})
      }).then(r => r.json()).catch(() => ({need_client_fetch: true}));

      const d = await serverPromise;
      if (d.title) {
        currentTitle = d.title;
        document.getElementById("lectureTitleDisplay").innerText = currentTitle;
      }

      if (d.subtitles && d.subtitles.length) {
        applyLoadedLecture(d);
        return;
      }

      const rawSubs = await fetchFromPhoneBrowser(vid);
      if (rawSubs && rawSubs.length) {
        subtitles = rawSubs.map(s => ({...s, vi: s.vi || "⏳ Đang dịch...", translated: Boolean(s.translated)}));
        document.getElementById("pasteRawArea").value = subtitles.map(s => `${formatTime(s.start)} ${s.text}`).join("\n");
        renderSubs();
        startBgTrans();
        return;
      }

      box.innerHTML = `<div class="text-center pt-8 px-4 space-y-3">
        <p class="text-amber-400 text-xs sm:text-sm leading-relaxed">Video này hiện bị tường lửa YouTube chặn trên IP đám mây. Bạn hãy bấm nút bên dưới để AI nghe và dịch trực tiếp theo thời gian thực nhé!</p>
        <div class="flex justify-center gap-2 pt-2">
          <button onclick="toggleLiveMicTranscribe()" class="bg-amber-600 hover:bg-amber-500 text-white text-xs px-4 py-2 rounded-lg font-bold shadow-lg">🎙️ Bật AI Nghe Loa (Dịch Tức Thì)</button>
          <button onclick="openPasteModal()" class="bg-purple-600 hover:bg-purple-500 text-white text-xs px-3.5 py-2 rounded-lg font-bold">📋 Dán Transcript</button>
        </div>
      </div>`;
      document.getElementById("subCount").innerText = "0 câu";
    }

    async function buildAllMedicalTabs() {
      if (!subtitles.length) return;

      const freqMap = {};
      subtitles.forEach((s, idx) => {
        const words = s.text.toLowerCase().replace(/[^a-z\s]/g, "").split(/\s+/);
        words.forEach(w => {
          if (w.length >= 6 && !STOP_WORDS.has(w)) {
            if (!freqMap[w]) {
              freqMap[w] = {word: w, meaning: "", count: 0, firstStart: s.start, firstIdx: idx, sample: s.text};
            }
            freqMap[w].count++;
          }
        });
      });

      const topWords = Object.values(freqMap).sort((a, b) => b.count - a.count).slice(0, 20);
      document.getElementById("vocabTabCount").innerText = topWords.length;

      if (topWords.length > 0) {
        const tl = document.getElementById("targetLang").value || "vi";
        const block = topWords.map(x => x.word).join("\n");
        const transStr = await fastTranslateClient(block, tl);
        const transArr = transStr.split("\n");
        topWords.forEach((item, i) => {
          item.meaning = (transArr[i] || item.word).trim();
        });
        extractedVocabList = topWords;
      }

      const vBox = document.getElementById("vocabListContainer");
      if (!topWords.length) {
        vBox.innerHTML = `<p class="text-slate-400 text-xs text-center pt-10">Chưa có từ vựng nào.</p>`;
      } else {
        vBox.innerHTML = topWords.map(item => `
          <div class="p-2.5 rounded-lg bg-slate-950 border border-slate-800 flex justify-between items-center gap-2">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <span class="font-bold text-yellow-400 text-sm capitalize">${item.word}</span>
                <span class="text-[11px] bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded-full">Lặp lại ${item.count} lần</span>
                <span class="text-[11px] text-slate-400 font-mono">${formatTime(item.firstStart)}</span>
              </div>
              <p class="text-xs sm:text-sm text-emerald-400 font-semibold mt-0.5">${item.meaning}</p>
              <p class="text-[11px] text-slate-400 italic mt-0.5 truncate">"${item.sample}"</p>
            </div>
            <button onclick="playSeg(${item.firstStart}, 5, ${item.firstIdx})" class="bg-slate-800 hover:bg-blue-600 text-xs px-2.5 py-1.5 rounded-lg shrink-0">🔊 Nghe</button>
          </div>
        `).join("");
      }

      const wbwBox = document.getElementById("wordByWordList");
      wbwBox.innerHTML = subtitles.slice(0, 60).map((s, idx) => {
        const tokens = s.text.split(/\s+/).map(tok => {
          const clean = tok.replace(/[^a-zA-Z0-9-]/g, "");
          return `<span onclick="lookupWord(event,'${clean}',${idx})" class="cursor-pointer inline-flex items-center bg-slate-900 border border-slate-800 px-2 py-1 rounded mr-1 mb-1 hover:border-yellow-400 text-xs font-semibold text-white">${tok}</span>`;
        }).join("");
        return `<div class="p-2.5 rounded-lg bg-slate-950 border border-slate-800">
          <div class="flex justify-between items-center text-[11px] text-slate-400 mb-1.5">
            <span>Câu #${idx + 1} • ⏱️ ${formatTime(s.start)}</span>
            <button onclick="playSeg(${s.start},${s.duration},${idx})" class="text-blue-400 hover:underline">🔊 Phát câu này</button>
          </div>
          <div class="flex flex-wrap">${tokens}</div>
          <div id="wbw-vi-${idx}" class="text-xs text-emerald-400 font-medium mt-1.5 pt-1.5 border-t border-slate-900">${s.vi}</div>
        </div>`;
      }).join("");

      updateAnalyticalTabs();
      generateQuiz();
    }

    function updateAnalyticalTabs() {
      if (!subtitles.length) return;
      const third = Math.max(1, Math.floor(subtitles.length / 3));
      const p1 = subtitles.slice(0, Math.min(4, third)).map(s => s.vi && !s.vi.includes("⏳") ? s.vi : s.text).join(" ");
      const p2 = subtitles.slice(third, Math.min(third + 4, subtitles.length)).map(s => s.vi && !s.vi.includes("⏳") ? s.vi : s.text).join(" ");
      const p3 = subtitles.slice(Math.max(0, subtitles.length - 4)).map(s => s.vi && !s.vi.includes("⏳") ? s.vi : s.text).join(" ");

      document.getElementById("summaryContainer").innerHTML = `
        <div class="p-3 rounded-xl bg-slate-950 border border-slate-800 space-y-1">
          <span class="text-xs font-bold text-blue-400">1. Phần mở đầu</span>
          <p class="text-xs text-slate-300 leading-relaxed">${p1}</p>
        </div>
        <div class="p-3 rounded-xl bg-slate-950 border border-slate-800 space-y-1">
          <span class="text-xs font-bold text-emerald-400">2. Nội dung trọng tâm</span>
          <p class="text-xs text-slate-300 leading-relaxed">${p2}</p>
        </div>
        <div class="p-3 rounded-xl bg-slate-950 border border-slate-800 space-y-1">
          <span class="text-xs font-bold text-amber-400">3. Phần tổng kết</span>
          <p class="text-xs text-slate-300 leading-relaxed">${p3}</p>
        </div>
      `;

      const clinRx = /\b(patient|lesion|polyp|biopsy|resection|endoscop|ultrasound|diagnos|treatment|bleeding|mucosa|disease|therapy|clinical|hospital|doctor|surgery|transfusion)\b/i;
      const clinItems = subtitles.map((s, idx) => ({...s, idx})).filter(s => clinRx.test(s.text)).slice(0, 25);
      document.getElementById("clinicalContainer").innerHTML = (clinItems.length ? clinItems : subtitles.slice(0, 10).map((s, idx) => ({...s, idx}))).map(s => `
        <div onclick="playSeg(${s.start},${s.duration},${s.idx})" class="p-2.5 rounded-lg bg-slate-950 border border-rose-500/30 hover:border-rose-400 cursor-pointer">
          <div class="flex justify-between text-[11px] text-rose-300 font-semibold mb-1">
            <span>🏥 Phân cảnh lâm sàng • ⏱️ ${formatTime(s.start)}</span>
            <span>🔊 Xem phân cảnh</span>
          </div>
          <p class="text-xs text-white font-medium">${s.text}</p>
          <p class="text-xs text-emerald-400 mt-1">${s.vi}</p>
        </div>
      `).join("");

      const resRx = /(\d+%|\bstudy\b|\btrial\b|\brandomized\b|\bguideline\b|\brisk\b|\bscore\b|\bdata\b|\bresult\b|\brate\b)/i;
      const resItems = subtitles.map((s, idx) => ({...s, idx})).filter(s => resRx.test(s.text)).slice(0, 25);
      document.getElementById("researchContainer").innerHTML = (resItems.length ? resItems : subtitles.slice(0, 8).map((s, idx) => ({...s, idx}))).map(s => `
        <div onclick="playSeg(${s.start},${s.duration},${s.idx})" class="p-2.5 rounded-lg bg-slate-950 border border-purple-500/30 hover:border-purple-400 cursor-pointer">
          <div class="flex justify-between text-[11px] text-purple-300 font-semibold mb-1">
            <span>🔬 Dữ liệu & Khuyến cáo • ⏱️ ${formatTime(s.start)}</span>
            <span>🔊 Xem dẫn chứng</span>
          </div>
          <p class="text-xs text-white font-medium">${s.text}</p>
          <p class="text-xs text-emerald-400 mt-1">${s.vi}</p>
        </div>
      `).join("");
    }

    function generateQuiz() {
      const qBox = document.getElementById("quizContainer");
      if (extractedVocabList.length < 4) {
        qBox.innerHTML = `<p class="text-slate-400 text-xs text-center pt-10">Cần ít nhất 4 câu phụ đề để hệ thống trích xuất câu hỏi trắc nghiệm từ video.</p>`;
        return;
      }
      const shuffled = [...extractedVocabList].sort(() => Math.random() - 0.5).slice(0, 5);
      const allMeanings = extractedVocabList.map(x => x.meaning);

      qBox.innerHTML = shuffled.map((q, qIdx) => {
        const wrong = allMeanings.filter(m => m !== q.meaning).sort(() => Math.random() - 0.5).slice(0, 3);
        const options = [q.meaning, ...wrong].sort(() => Math.random() - 0.5);
        return `<div class="p-3 rounded-xl bg-slate-950 border border-slate-800 space-y-2">
          <p class="text-xs font-bold text-yellow-400">Câu ${qIdx + 1}: Từ <span class="underline text-white">"${q.word.toUpperCase()}"</span> trong video có nghĩa là gì?</p>
          <p class="text-[11px] text-slate-400 italic">Câu gốc trong video: "${q.sample}"</p>
          <div class="grid grid-cols-1 gap-1.5 pt-1">
            ${options.map(opt => `
              <button onclick="checkQuizAnswer(this, '${opt === q.meaning ? '1' : '0'}', '${q.meaning.replace(/'/g, "")}')" class="text-left text-xs p-2 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-200 transition">
                • ${opt}
              </button>
            `).join("")}
          </div>
        </div>`;
      }).join("");
    }

    function checkQuizAnswer(btn, isCorrect, rightAns) {
      const parent = btn.parentElement;
      Array.from(parent.children).forEach(b => b.disabled = true);
      if (isCorrect === '1') {
        btn.className = "text-left text-xs p-2 rounded-lg bg-emerald-600 text-white font-bold border border-emerald-400";
        btn.innerText += " ✅ Chính xác!";
      } else {
        btn.className = "text-left text-xs p-2 rounded-lg bg-rose-600 text-white font-bold border border-rose-400";
        btn.innerText += ` ❌ Sai (Đáp án: ${rightAns})`;
      }
    }

    function saveCurrentLecture() {
      if (!subtitles.length) return alert("Chưa có dữ liệu bài giảng để lưu!");
      const saved = JSON.parse(localStorage.getItem("medlingua_saved_lectures") || "[]");
      const filtered = saved.filter(x => x.video_id !== currentVideoId);
      filtered.unshift({
        video_id: currentVideoId,
        title: currentTitle,
        date: new Date().toLocaleString("vi-VN"),
        count: subtitles.length,
        subtitles: subtitles
      });
      localStorage.setItem("medlingua_saved_lectures", JSON.stringify(filtered.slice(0, 15)));
      updateSavedBadge();
      alert("✅ Đã lưu bản dịch bài giảng vào bộ nhớ!");
    }

    function updateSavedBadge() {
      const saved = JSON.parse(localStorage.getItem("medlingua_saved_lectures") || "[]");
      document.getElementById("savedCountBadge").innerText = saved.length;
    }

    function openSavedModal() {
      const saved = JSON.parse(localStorage.getItem("medlingua_saved_lectures") || "[]");
      const box = document.getElementById("savedListBox");
      if (!saved.length) {
        box.innerHTML = `<p class="text-slate-400 text-xs text-center py-8">Chưa có bài giảng nào được lưu.</p>`;
      } else {
        box.innerHTML = saved.map((item, idx) => `
          <div class="p-3 rounded-xl bg-slate-950 border border-slate-800 flex justify-between items-center gap-2">
            <div class="min-w-0 flex-1">
              <p class="font-bold text-sm text-blue-400 truncate">${item.title}</p>
              <p class="text-xs text-slate-400">${item.count} phân cảnh • Lưu lúc: ${item.date}</p>
            </div>
            <div class="flex gap-1.5 shrink-0">
              <button onclick="loadSavedItem(${idx})" class="bg-emerald-600 hover:bg-emerald-500 text-white text-xs px-3 py-1.5 rounded-lg font-bold">Mở lại</button>
              <button onclick="deleteSavedItem(${idx})" class="bg-rose-600/20 text-rose-300 text-xs px-2.5 py-1.5 rounded-lg">Xóa</button>
            </div>
          </div>
        `).join("");
      }
      document.getElementById("savedModal").classList.remove("hidden");
    }

    function closeSavedModal() {
      document.getElementById("savedModal").classList.add("hidden");
    }

    function loadSavedItem(idx) {
      const saved = JSON.parse(localStorage.getItem("medlingua_saved_lectures") || "[]");
      const item = saved[idx];
      if (!item) return;
      closeSavedModal();
      clearAllSubtitles();
      initPlayer(item.video_id);
      applyLoadedLecture(item);
    }

    function deleteSavedItem(idx) {
      const saved = JSON.parse(localStorage.getItem("medlingua_saved_lectures") || "[]");
      saved.splice(idx, 1);
      localStorage.setItem("medlingua_saved_lectures", JSON.stringify(saved));
      updateSavedBadge();
      openSavedModal();
    }

    function exportVocabTxt() {
      const lines = [`SỔ TỪ VỰNG BÀI GIẢNG: ${currentTitle}\nURL: https://www.youtube.com/watch?v=${currentVideoId}\n====================================\n`];
      extractedVocabList.forEach(item => {
        lines.push(`- ${item.word.toUpperCase()}: ${item.meaning} (${formatTime(item.firstStart)})`);
      });
      const blob = new Blob([lines.join("\n")], {type: "text/plain;charset=utf-8"});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `Tu_Vung_${currentVideoId}.txt`;
      a.click();
    }

    function filterSubtitles(kw) {
      const q = (kw || "").toLowerCase().trim();
      subtitles.forEach((s, i) => {
        const el = document.getElementById("sub-" + i);
        if (!el) return;
        const match = !q || s.text.toLowerCase().includes(q) || (s.vi || "").toLowerCase().includes(q);
        el.classList.toggle("hidden", !match);
      });
    }

    function initPlayer(vid) {
      if (player && player.loadVideoById) {
        player.loadVideoById(vid);
      } else if (window.YT && YT.Player) {
        player = new YT.Player('player', {
          height: '100%',
          width: '100%',
          videoId: vid,
          playerVars: {playsinline: 1, rel: 0},
          events: {
            onStateChange: e => {
              if (e.data === 2 || e.data === 0) stopCabin();
              if (e.data === 1 && player.getVideoData) {
                const playingId = player.getVideoData().video_id;
                if (playingId && playingId !== currentVideoId) {
                  document.getElementById("videoUrl").value = "https://www.youtube.com/watch?v=" + playingId;
                  processUrlOrId("https://www.youtube.com/watch?v=" + playingId);
                }
              }
            }
          }
        });
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
        txt.innerText = "🎧 Bật Dịch Cabin (Nghe Thuyết Minh Song Song)";
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
      const tl = document.getElementById("targetLang").value || "vi";
      cabinAudio.src = `/api/tts?tl=${tl}&text=` + encodeURIComponent(viText);
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
      if (!url) return alert("Bạn hãy dán link YouTube vào ô YouTube URL nhé!");
      const btn = document.getElementById("btnLoad");
      const oldTxt = btn.innerText;
      btn.innerText = "⏳ Đang trích xuất..."; btn.disabled = true; stopCabin();
      try { await processUrlOrId(url); }
      catch (e) { alert("Lỗi kết nối: " + e.message); }
      finally { btn.innerText = oldTxt; btn.disabled = false; }
    }

    async function startBgTrans() {
      const cur = ++jobId;
      const targetLang = document.getElementById("targetLang").value || "vi";
      while (cur === jobId) {
        const todo = [];
        for (let i = 0; i < subtitles.length && todo.length < 25; i++) {
          const idx = (activeSubIndex + i) % subtitles.length;
          if (!subtitles[idx].translated) todo.push(idx);
        }
        const done = subtitles.filter(s => s.translated).length;
        document.getElementById("subCount").innerText = done < subtitles.length ? `⚡ ${done}/${subtitles.length} câu` : `${subtitles.length} câu`;
        if (!todo.length) {
          if (currentTab !== 'scene') buildAllMedicalTabs();
          break;
        }
        try {
          const block = todo.map((idx, k) => `[${k}] ${subtitles[idx].text}`).join("\n");
          const transBlock = await fastTranslateClient(block, targetLang);
          if (cur !== jobId) return;
          const lines = transBlock.split("\n");
          todo.forEach((subIdx, k) => {
            const rawLine = (lines[k] || "").replace(/^\[\s*\d+\s*\]\s*/, "").trim();
            const finalVi = rawLine || subtitles[subIdx].text;
            subtitles[subIdx].vi = finalVi;
            subtitles[subIdx].translated = true;
            const el = document.getElementById("sub-vi-" + subIdx);
            if (el) el.innerText = finalVi;
          });
        } catch (e) {
          await new Promise(r => setTimeout(r, 400));
        }
      }
    }

    function renderSubs() {
      const box = document.getElementById("subtitleList");
      box.innerHTML = "";
      subtitles.forEach((s, i) => {
        box.appendChild(createSubNode(s, i));
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
      if (!player || !player.getCurrentTime || isLiveListening) return;
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
      document.getElementById("wMeaning").innerText = "Đang tra cứu...";
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
