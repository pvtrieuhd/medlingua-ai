import re, json, html, socket, base64, sys, subprocess, threading, urllib.parse, urllib3, requests
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

def _bg_upgrade_tools():
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--upgrade", "youtube-transcript-api", "yt-dlp"], timeout=60)
    except Exception:
        pass

threading.Thread(target=_bg_upgrade_tools, daemon=True).start()

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
ANDROID_UA = "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip"
IOS_UA = "com.google.ios.youtube/20.10.4 (iPhone16,2; U; CPU iOS 18_2_1 like Mac OS X;)"
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
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", u):
        return u
    return None

def safe_req(method: str, url: str, **kw):
    kw.setdefault("timeout", 9)
    try:
        return requests.request(method, url, **kw)
    except Exception:
        kw["verify"] = False
        return requests.request(method, url, **kw)

def resolve_universal_url(raw_url: str) -> dict:
    """
    Giải mã mọi loại đường dẫn:
    - Link rút gọn (link.z-waka.com, bit.ly, tinyurl, t.co...)
    - Link YouTube (watch, live, shorts, youtu.be)
    - Link phòng họp trực tiếp (Zoom, Google Meet, Microsoft Teams, Webex, Z-Waka)
    - Link video trực tiếp (.mp4, .m3u8) hoặc trang web có nhúng video
    """
    u = (raw_url or "").strip()
    if not u:
        return {"type": "invalid"}
    if not u.startswith(("http://", "https://")):
        vid = extract_youtube_id(u)
        if vid:
            return {"type": "youtube", "video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": ""}
        u = "https://" + u

    vid = extract_youtube_id(u)
    if vid:
        return {"type": "youtube", "video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": ""}

    final_url = u
    page_title = ""
    page_html = ""

    # Đi theo chuỗi Redirect (301/302) để giải mã link rút gọn như link.z-waka.com
    try:
        r = safe_req("GET", u, headers={"User-Agent": UA}, allow_redirects=True, timeout=8, stream=True)
        final_url = r.url or u
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype:
            raw_bytes = b""
            for chunk in r.iter_content(chunk_size=16384):
                raw_bytes += chunk
                if len(raw_bytes) > 131072:
                    break
            page_html = raw_bytes.decode("utf-8", errors="ignore")
        r.close()
    except Exception:
        pass

    # Kiểm tra xem URL sau khi chuyển hướng có phải là YouTube không
    vid_after = extract_youtube_id(final_url)
    if vid_after:
        return {"type": "youtube", "video_id": vid_after, "url": f"https://www.youtube.com/watch?v={vid_after}", "title": ""}

    # Kiểm tra meta refresh hoặc iframe YouTube nhúng bên trong trang đích (ví dụ trang sự kiện z-waka)
    if page_html:
        m_title = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.I | re.S)
        if m_title:
            page_title = re.sub(r"\s+", " ", html.unescape(m_title.group(1))).strip()

        m_refresh = re.search(r'url=([^"\'>\s]+)', page_html, re.I)
        if m_refresh:
            ref_u = html.unescape(m_refresh.group(1))
            vid_ref = extract_youtube_id(ref_u)
            if vid_ref:
                return {"type": "youtube", "video_id": vid_ref, "url": f"https://www.youtube.com/watch?v={vid_ref}", "title": page_title}
            if ref_u.startswith("http"):
                final_url = ref_u

        m_yt_embed = re.search(r'(?:youtube\.com/embed/|youtube\.com/watch\?v=|youtu\.be/)([0-9A-Za-z_-]{11})', page_html)
        if m_yt_embed:
            vid_emb = m_yt_embed.group(1)
            return {"type": "youtube", "video_id": vid_emb, "url": f"https://www.youtube.com/watch?v={vid_emb}", "title": page_title}

        m_media = re.search(r'(https?://[^\s"\'<>]+\.(?:m3u8|mp4)(?:\?[^\s"\'<>]+)?)', page_html, re.I)
        if m_media:
            return {"type": "direct_media", "video_id": "media_stream", "url": m_media.group(1), "orig_url": final_url, "title": page_title or "ồng phát Video trực tiếp"}

    low_u = final_url.lower()
    if any(k in low_u for k in [".mp4", ".m3u8", ".webm"]):
        return {"type": "direct_media", "video_id": "media_stream", "url": final_url, "orig_url": final_url, "title": page_title or "Video trực tiếp"}

    platform_name = "Phòng Họp / Livestream Trực Tiếp"
    if "zoom.us" in low_u:
        platform_name = "Zoom Webinar / Meeting Trực Tiếp"
    elif "meet.google.com" in low_u:
        platform_name = "Google Meet Trực Tiếp"
    elif "teams.microsoft.com" in low_u or "teams.live.com" in low_u:
        platform_name = "Microsoft Teams Trực Tiếp"
    elif "z-waka.com" in low_u:
        platform_name = "Hội Thảo Y Khoa Z-Waka"
    elif "webex.com" in low_u:
        platform_name = "Cisco Webex Webinar"

    return {
        "type": "external_live",
        "video_id": "live_" + str(abs(hash(final_url)) % 1000000),
        "url": final_url,
        "platform": platform_name,
        "title": page_title or platform_name
    }

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
    return f"Bài giảng Video ({vid})"

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

def build_get_transcript_params(video_id: str, kind: str = "asr", lang: str = "en", url_encode_inner: bool = True) -> str:
    """
    Chuẩn Protobuf 8 trường đầy đủ của YouTube Transcript API (Bao gồm trường 6=1, 7=1, 8=1):
    Field 1: video_id
    Field 2: base64(Field 1: kind, Field 2: lang, Field 3: "")
    Field 3: varint 1 (\x18\x01)
    Field 5: "engagement-panel-searchable-transcript-search-panel"
    Field 6: varint 1 (\x30\x01)
    Field 7: varint 1 (\x38\x01)
    Field 8: varint 1 (\x40\x01)
    """
    inner = _pb_str(1, kind) + _pb_str(2, lang) + _pb_str(3, "")
    raw_b64 = base64.b64encode(inner).decode("ascii")
    inner_str = urllib.parse.quote(raw_b64) if url_encode_inner else raw_b64
    outer = (
        _pb_str(1, video_id)
        + _pb_str(2, inner_str)
        + b"\x18\x01"
        + _pb_str(5, "engagement-panel-searchable-transcript-search-panel")
        + b"\x30\x01\x38\x01\x40\x01"
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
# CÁC LUỒNG TRÍCH XUẤT PHỤ ĐỀ VƯỢT TƯỜNG LỬA CHO CẢ RENDER CLOUD & MOBILE 4G
# ==============================================================================

def w_get_transcript_panel(vid: str) -> list[dict]:
    """Luồng 1: Gọi cổng /youtubei/v1/get_transcript với Protobuf 8 trường đầy đủ (Không bị khóa bởi sparams=ip)"""
    params_list = [
        build_get_transcript_params(vid, kind="asr", lang="en", url_encode_inner=True),
        build_get_transcript_params(vid, kind="", lang="en", url_encode_inner=True),
        build_get_transcript_params(vid, kind="asr", lang="en", url_encode_inner=False),
        build_get_transcript_params(vid, kind="", lang="en", url_encode_inner=False)
    ]
    clients = [
        ("WEB", WEB_CLIENT_VERSION, UA),
        ("MWEB", "2.20260115.01.00", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    ]
    endpoints = [
        "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        "https://m.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        "https://api.cors.lol/?url=" + urllib.parse.quote("https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false", safe="")
    ]
    for ep in endpoints:
        for c_name, c_ver, c_ua in clients:
            for p_val in params_list:
                try:
                    payload = {
                        "context": {"client": {"clientName": c_name, "clientVersion": c_ver, "hl": "en", "gl": "US"}},
                        "params": p_val
                    }
                    r = safe_req("POST", ep, json=payload, headers={"User-Agent": c_ua, "Content-Type": "application/json", "Origin": "https://www.youtube.com"}, timeout=8)
                    if r.status_code == 200 and "transcriptSegmentRenderer" in r.text:
                        subs = parse_get_transcript_json(r.json())
                        if subs:
                            return subs
                except Exception:
                    pass
    return []

def w_google_translate_proxy_synced(vid: str) -> list[dict]:
    """
    Luồng 2: Đi xuyên qua Google Translate Proxy cho CẢ 2 BƯỚC (Watch + Timedtext)
    để IP tải phụ đề trùng khớp 100% với IP của Google đã ký trong sparams=ip!
    """
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    gt_watch = f"https://www-youtube-com.translate.goog/watch?v={vid}&hl=en&_x_tr_sl=es&_x_tr_tl=en&_x_tr_hl=en"
    try:
        r = s.get(gt_watch, timeout=10)
        if r.status_code == 200:
            # Nếu lấy được getTranscriptEndpoint.params từ trang Watch thì gọi get_transcript ngay
            for m_p in re.finditer(r'"getTranscriptEndpoint"\s*:\s*\{\s*"params"\s*:\s*"([^"]+)"', r.text):
                p_raw = urllib.parse.unquote(m_p.group(1))
                r_gt = s.post(
                    "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
                    json={"context": {"client": {"clientName": "WEB", "clientVersion": WEB_CLIENT_VERSION, "hl": "en", "gl": "US"}}, "params": p_raw},
                    timeout=8
                )
                if r_gt.status_code == 200:
                    subs = parse_get_transcript_json(r_gt.json())
                    if subs:
                        s.close()
                        return subs

            # Bóc baseUrl và định tuyến lại qua www-youtube-com.translate.goog để giữ nguyên IP của Google
            urls = re.findall(r'"baseUrl"\s*:\s*"(https://www\.youtube\.com/api/timedtext[^"]+)"', r.text)
            for raw_u in urls:
                clean_u = raw_u.replace("\\u0026", "&").replace("\\/", "/").replace("&fmt=srv3", "")
                if "exp=xpe" not in clean_u:
                    proxied_timedtext = clean_u.replace("https://www.youtube.com/api/timedtext", "https://www-youtube-com.translate.goog/api/timedtext")
                    proxied_timedtext += "&_x_tr_sl=es&_x_tr_tl=en&_x_tr_hl=en"
                    for fmt_try in [with_format(proxied_timedtext, "json3"), proxied_timedtext]:
                        r_sub = s.get(fmt_try, timeout=9)
                        if r_sub.status_code == 200 and r_sub.text.strip():
                            subs = parse_captions(r_sub.text)
                            if subs:
                                s.close()
                                return subs
    except Exception:
        pass
    s.close()
    return []

def w_android_single_session(vid: str) -> list[dict]:
    """Luồng 3: Giả lập ứng dụng ANDROID / IOS / ANDROID_VR trên cùng 1 Session IPv4"""
    clients = [
        ({"clientName": "ANDROID", "clientVersion": "20.10.38", "hl": "en", "gl": "US"}, ANDROID_UA),
        ({"clientName": "IOS", "clientVersion": "20.10.4", "hl": "en", "gl": "US"}, IOS_UA),
        ({"clientName": "ANDROID_VR", "clientVersion": "1.61.48", "androidSdkVersion": 32, "hl": "en", "gl": "US"}, ANDROID_UA)
    ]
    for cl, c_ua in clients:
        s = requests.Session()
        s.headers.update({"User-Agent": c_ua, "Accept-Language": "en-US,en;q=0.9"})
        try:
            r = s.post("https://www.youtube.com/youtubei/v1/player?prettyPrint=false", json={"context": {"client": cl}, "videoId": vid}, timeout=8)
            if r.status_code == 200:
                tracks = r.json().get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
                if tracks:
                    tr = next((x for x in tracks if x.get("languageCode", "").startswith("en") and x.get("kind") != "asr"), None)
                    if not tr:
                        tr = next((x for x in tracks if x.get("languageCode", "").startswith("en")), tracks[0])
                    base_url = tr.get("baseUrl", "").replace("\\u0026", "&").replace("\\/", "/")
                    if base_url and "exp=xpe" not in base_url:
                        for target in [with_format(base_url, "json3"), base_url.replace("&fmt=srv3", "")]:
                            r_sub = s.get(target, timeout=8)
                            if r_sub.status_code == 200 and r_sub.text.strip():
                                subs = parse_captions(r_sub.text)
                                if subs:
                                    s.close()
                                    return subs
        except Exception:
            pass
        finally:
            s.close()
    return []

def w_ytdlp_universal(url_or_vid: str) -> list[dict]:
    """Luồng 4: Dùng yt-dlp trích xuất phụ đề cho cả YouTube và các nền tảng video khác"""
    target_url = f"https://www.youtube.com/watch?v={url_or_vid}" if re.fullmatch(r"[0-9A-Za-z_-]{11}", url_or_vid) else url_or_vid
    try:
        import yt_dlp
        ydl_opts = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en.*", "en", "vi"],
            "extractor_args": {"youtube": {"player_client": ["ios", "android_vr", "mweb", "tv_embedded"]}}
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)
            for key in ["subtitles", "automatic_captions"]:
                sub_dict = info.get(key) or {}
                for lang_k in sub_dict:
                    if lang_k.startswith(("en", "vi")):
                        formats = sub_dict[lang_k]
                        for fmt_ext in ["json3", "srv1", "vtt", "ttml"]:
                            chosen = next((f for f in formats if f.get("ext") == fmt_ext), None)
                            if chosen and chosen.get("url") and "exp=xpe" not in chosen["url"]:
                                r = safe_req("GET", chosen["url"], headers={"User-Agent": UA}, timeout=9)
                                subs = parse_captions(r.text)
                                if subs:
                                    return subs
    except Exception:
        pass
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

def w_proxy_watch_synced(vid: str, proxy_tpl: str) -> list[dict]:
    """Lấy trang Watch và tải Timedtext qua CÙNG MỘT PROXY để giữ nguyên địa chỉ IP trong sparams=ip"""
    w_url = f"https://www.youtube.com/watch?v={vid}&hl=en"
    try:
        r1 = safe_req("GET", proxy_tpl.format(urllib.parse.quote(w_url, safe="")), headers={"User-Agent": UA}, timeout=10)
        if r1.status_code == 200 and '"captionTracks":' in r1.text:
            urls = re.findall(r'"baseUrl"\s*:\s*"(https://www\.youtube\.com/api/timedtext[^"]+)"', r1.text)
            for raw_u in urls:
                clean_u = raw_u.replace("\\u0026", "&").replace("\\/", "/").replace("&fmt=srv3", "")
                if "exp=xpe" not in clean_u:
                    for target_u in [with_format(clean_u, "json3"), clean_u]:
                        r2 = safe_req("GET", proxy_tpl.format(urllib.parse.quote(target_u, safe="")), headers={"User-Agent": UA}, timeout=10)
                        if r2.status_code == 200 and r2.text.strip():
                            subs = parse_captions(r2.text)
                            if subs:
                                return subs
    except Exception:
        pass
    return []

def fetch_youtube_subtitles(video_id: str) -> list[dict]:
    tasks = [
        lambda: w_get_transcript_panel(video_id),
        lambda: w_google_translate_proxy_synced(video_id),
        lambda: w_android_single_session(video_id),
        lambda: w_ytapi(video_id),
        lambda: w_ytdlp_universal(video_id),
        lambda: w_tactiq_direct(video_id),
        lambda: w_notegpt_direct(video_id),
        lambda: w_proxy_watch_synced(video_id, "https://api.codetabs.com/v1/proxy?quest={}"),
        lambda: w_proxy_watch_synced(video_id, "https://api.allorigins.win/raw?url={}")
    ]
    ex = ThreadPoolExecutor(max_workers=9)
    try:
        futures = [ex.submit(fn) for fn in tasks]
        for fut in as_completed(futures, timeout=16):
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

def prepare_initial_subs(vid: str, raw_subs: list[dict], title: str, target_lang: str = "vi", source_type: str = "youtube", media_url: str = ""):
    subs = raw_subs[:3500]
    for s in subs:
        s["text"] = clean_txt(s.get("text", "")) or "..."
    init_n = min(20, len(subs))
    init_vi = translate_batch_list([subs[i]["text"] for i in range(init_n)], batch_size=20, tl=target_lang)
    for i, s in enumerate(subs):
        s["vi"] = init_vi[i] if i < init_n else "⏳ Đang dịch..."
        s["translated"] = (i < init_n)
    return {
        "source_type": source_type,
        "video_id": vid,
        "media_url": media_url,
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
    resolved = resolve_universal_url(req.url)
    if resolved["type"] == "invalid":
        return {"error": "Bạn hãy dán một đường dẫn hợp lệ vào ô URL nhé!"}

    # Trường hợp 1: Link dẫn tới YouTube (Bao gồm cả link rút gọn z-waka/bit.ly trỏ về YouTube)
    if resolved["type"] == "youtube":
        vid = resolved["video_id"]
        title = fetch_video_title(vid, req.custom_title or resolved.get("title", ""))
        try:
            subs = fetch_youtube_subtitles(vid)
            return prepare_initial_subs(vid, subs, title, req.target_lang, source_type="youtube", media_url=resolved["url"])
        except Exception:
            return {
                "need_client_fetch": True,
                "source_type": "youtube",
                "video_id": vid,
                "media_url": resolved["url"],
                "title": title,
                "pb_asr": build_get_transcript_params(vid, "asr", "en", True),
                "pb_man": build_get_transcript_params(vid, "", "en", True),
                "pb_asr_raw": build_get_transcript_params(vid, "asr", "en", False)
            }

    # Trường hợp 2: Link Video trực tiếp (.mp4, .m3u8) hoặc nền tảng khác
    if resolved["type"] == "direct_media":
        title = req.custom_title or resolved.get("title") or "Video Trực Tiếp"
        subs = w_ytdlp_universal(resolved.get("orig_url") or resolved["url"])
        if subs:
            return prepare_initial_subs(resolved["video_id"], subs, title, req.target_lang, source_type="direct_media", media_url=resolved["url"])
        return {
            "source_type": "direct_media",
            "video_id": resolved["video_id"],
            "media_url": resolved["url"],
            "title": title,
            "subtitles": []
        }

    # Trường hợp 3: Link Zoom / Google Meet / Microsoft Teams / Hội thảo Z-Waka đang phát trực tiếp
    title = req.custom_title or resolved.get("title") or resolved.get("platform") or "Phòng Họp Trực Tuyến"
    subs = w_ytdlp_universal(resolved["url"])
    if subs:
        return prepare_initial_subs(resolved["video_id"], subs, title, req.target_lang, source_type="external_live", media_url=resolved["url"])
    return {
        "source_type": "external_live",
        "video_id": resolved["video_id"],
        "media_url": resolved["url"],
        "platform": resolved.get("platform", "Phòng Họp / Livestream"),
        "title": title,
        "subtitles": []
    }

@app.post("/api/process-raw-subtitles")
async def process_raw_subtitles(req: RawSubsRequest):
    title = fetch_video_title(req.video_id, req.custom_title)
    if is_valid_subs(req.subtitles):
        return prepare_initial_subs(req.video_id, req.subtitles, title, req.target_lang)
    return {
        "error": "Video này không mở phụ đề công khai hoặc đang phát trực tiếp. Bạn hãy bấm nút '🎙️ AI Nghe Loa' để nghe và dịch trực tiếp theo thời gian thực nhé!"
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
  <title>MedLingua AI Studio - Dịch Mọi Nguồn Video, Zoom, Meet, Teams & YouTube</title>
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
            <span class="text-[11px] bg-emerald-500/20 text-emerald-400 px-2.5 py-0.5 rounded-full font-medium">YouTube • Zoom • Meet • Teams • Z-Waka</span>
          </h1>
          <p class="text-xs text-slate-400">Hỗ trợ mọi đường dẫn Video & Phòng họp trực tuyến • Tự động lấy CC hoặc AI Nghe & Dịch Cabin thời gian thực</p>
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
      <!-- Cột trái: Nạp Mọi Đường Dẫn -->
      <div class="lg:col-span-4 bg-slate-900 border border-slate-800 p-4 rounded-xl space-y-3 shadow-lg">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2">
          <div class="flex items-center gap-2 text-red-400 font-bold text-sm">
            <span>▶️</span>
            <span>Nạp Video / Phòng Họp Trực Tiếp</span>
          </div>
          <span class="text-[10px] bg-blue-600/30 text-blue-300 px-2 py-0.5 rounded font-semibold">Mọi Nguồn Link</span>
        </div>

        <div>
          <label class="block text-xs text-slate-300 font-medium mb-1">Tiêu đề bài giảng / Hội thảo (tùy chọn):</label>
          <input id="customTitle" type="text" placeholder="Nhập tên bài giảng hoặc để trống tự lấy..." class="w-full p-2.5 rounded-lg bg-slate-950 border border-slate-700 text-white text-xs focus:border-blue-500 focus:outline-none">
        </div>

        <div>
          <label class="block text-xs text-slate-300 font-medium mb-1">Đường dẫn (YouTube, Zoom, Meet, Teams, Z-Waka...):</label>
          <div class="relative">
            <input id="videoUrl" type="text" placeholder="Dán link YouTube, Zoom, Meet, Teams, z-waka..." class="w-full p-2.5 pr-8 rounded-lg bg-slate-950 border border-slate-700 text-white text-xs focus:border-blue-500 focus:outline-none">
            <button onclick="document.getElementById('videoUrl').value='';document.getElementById('videoUrl').focus();" class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white text-xs px-1">✕</button>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">Ngôn ngữ bài giảng</label>
            <select id="sourceLang" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200">
              <option value="en-US" selected>Tiếng Anh (US)</option>
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
            🚀 Mở Link & Dịch Phụ Đề
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
                <h2 id="lectureTitleDisplay" class="text-base sm:text-lg font-bold text-white truncate">Chưa nạp video / phòng họp</h2>
                <button onclick="analyzeVideo()" class="bg-slate-800 hover:bg-slate-700 text-xs text-blue-300 px-2.5 py-1 rounded-md font-semibold shrink-0">🔄 Tải lại</button>
                <button onclick="saveCurrentLecture()" class="bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 text-xs px-2.5 py-1 rounded-md font-semibold shrink-0">💾 Lưu bản dịch</button>
              </div>
              <p id="lectureUrlDisplay" class="text-xs text-slate-400 font-mono mt-0.5 truncate">Dán bất kỳ link YouTube, Zoom, Meet, Teams hoặc Z-Waka vào ô bên trái...</p>
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

    <!-- KHU VỰC HIỂN THỊ VIDEO / PHÒNG HỌP & CÁC TAB CHỨC NĂNG -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
      <!-- Khung Trình phát Đa Năng (YouTube / HTML5 Video / Live Meeting Studio) -->
      <div class="lg:col-span-7 space-y-3">
        <div class="aspect-video bg-black rounded-xl overflow-hidden border border-slate-800 shadow-2xl relative">
          <!-- 1. Khung YouTube -->
          <div id="ytWrapper" class="w-full h-full flex items-center justify-center text-slate-500 text-xs">
            <div id="player" class="w-full h-full flex items-center justify-center text-center px-4">
              Dán link YouTube, Zoom, Google Meet, Microsoft Teams hoặc Z-Waka và bấm "🚀 Mở Link & Dịch Phụ Đề"
            </div>
          </div>

          <!-- 2. Khung Video Trực Tiếp (.mp4 / .m3u8 / Chia sẻ màn hình phòng họp) -->
          <video id="html5Player" controls playsinline class="hidden w-full h-full bg-black object-contain"></video>

          <!-- 3. Khung Phòng Họp Trực Tuyến (Zoom / Google Meet / Teams / Z-Waka) -->
          <div id="liveMeetingOverlay" class="hidden absolute inset-0 bg-slate-950/95 flex flex-col items-center justify-center p-5 text-center space-y-3">
            <div class="w-12 h-12 rounded-2xl bg-blue-500/20 border border-blue-500/40 flex items-center justify-center text-2xl">📡</div>
            <h3 id="livePlatformTitle" class="text-base sm:text-lg font-bold text-blue-400">Phòng Họp / Hội Thảo Trực Tuyến</h3>
            <p id="livePlatformUrl" class="text-xs text-slate-400 font-mono max-w-md truncate"></p>
            <p class="text-xs text-slate-300 max-w-lg leading-relaxed">
              Để dịch trực tiếp buổi phát sóng này: Bạn hãy mở phòng họp, sau đó bấm <b>"🎙️ Bật AI Nghe & Dịch Cabin"</b> (hoặc <b>"🖥️ Chiếu Màn Hình Phòng Họp"</b> nếu dùng máy tính).
            </p>
            <div class="flex flex-wrap justify-center gap-2 pt-1">
              <a id="btnOpenExternalMeeting" href="#" target="_blank" rel="noopener" class="bg-blue-600 hover:bg-blue-500 text-white text-xs px-4 py-2.5 rounded-xl font-bold flex items-center gap-1.5 shadow-lg">
                🔗 1. Mở Phòng Họp (Tab Mới / App)
              </a>
              <button onclick="startScreenCaptureStream()" class="bg-teal-600 hover:bg-teal-500 text-white text-xs px-4 py-2.5 rounded-xl font-bold flex items-center gap-1.5 shadow-lg">
                🖥️ 2. Chiếu Màn Hình Phòng Họp Vào Đây
              </button>
              <button onclick="if(!isLiveListening) toggleLiveMicTranscribe();" class="bg-amber-600 hover:bg-amber-500 text-white text-xs px-4 py-2.5 rounded-xl font-bold flex items-center gap-1.5 shadow-lg">
                🎙️ 3. Bật AI Nghe & Dịch Trực Tiếp
              </button>
            </div>
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
            <p id="liveInterimText" class="text-xs text-white font-medium min-h-[18px]">Đang lắng nghe âm thanh...</p>
            <p id="liveInterimVi" class="text-xs text-emerald-400 font-semibold min-h-[18px]"></p>
          </div>

          <div id="subtitleList" class="flex-1 overflow-y-auto space-y-2 pr-1 relative">
            <p class="text-slate-400 text-xs sm:text-sm text-center pt-16">Hãy dán link vào ô bên trái và bấm "🚀 Mở Link & Dịch Phụ Đề" hoặc bật "🎙️ AI Nghe Loa".</p>
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
        • Bạn cũng có thể dán trực tiếp bản chép lời (Show transcript) vào đây để dịch ngay:
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
    let player = null, subtitles = [], currentVideoId = "", currentTitle = "", currentSourceType = "youtube";
    let activeTimer = null, activeSubIndex = 0, jobId = 0, currentTab = "scene";
    let cabinEnabled = false, isSpeaking = false, lastSpokenIdx = -1, duckVol = 20, cabinRate = 1.2;

    let speechRec = null, isLiveListening = false, liveStartTimestamp = 0;
    let streamResultIndex = -1, streamConsumedWords = 0, streamChunkStartSec = 0;
    let liveUnreadWords = [], interimTransTimer = null;
    let extractedVocabList = [];

    const cabinAudio = new Audio(), dictAudio = new Audio();
    cabinAudio.playsInline = true; dictAudio.playsInline = true;

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

    function getCurrentPlaybackSecond() {
      if (currentSourceType === "youtube" && player && player.getCurrentTime) {
        return Math.max(0, player.getCurrentTime() || 0);
      }
      const vEl = document.getElementById("html5Player");
      if (currentSourceType === "direct_media" && vEl && !vEl.classList.contains("hidden")) {
        return Math.max(0, vEl.currentTime || 0);
      }
      if (liveStartTimestamp > 0) {
        return Math.max(0, (Date.now() - liveStartTimestamp) / 1000);
      }
      return 0;
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

    // LẤY PHỤ ĐỀ TRỰC TIẾP TỪ TRÌNH DUYỆT ĐIỆN THOẠI 4G / PC (KHÔNG CẦN BẮC CẦU 127.0.0.1)
    async function fetchFromPhoneBrowser(vid, pbAsr, pbMan, pbAsrRaw) {
      const tasks = [
        // Luồng 1: Gọi /youtubei/v1/get_transcript với Protobuf 8 trường đầy đủ
        (async () => {
          const gtUrl = "https://api.cors.lol/?url=" + encodeURIComponent("https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false");
          for (const pVal of [pbAsr, pbMan, pbAsrRaw].filter(Boolean)) {
            try {
              const r = await fetch(gtUrl, {
                method: "POST",
                headers: {"Content-Type": "text/plain;charset=UTF-8"},
                body: JSON.stringify({
                  context: {client: {clientName: "WEB", clientVersion: "2.20260115.01.00", hl: "en", gl: "US"}},
                  params: pVal
                })
              });
              if (r.ok) {
                const subs = parseCapData(await r.text());
                if (subs.length) return subs;
              }
            } catch(e) {}
          }
          throw new Error("gt_empty");
        })(),
        // Luồng 2: Đi qua Google Translate Proxy đồng bộ cả Watch + Timedtext trên trình duyệt
        (async () => {
          const gtWatch = `https://www-youtube-com.translate.goog/watch?v=${vid}&hl=en&_x_tr_sl=es&_x_tr_tl=en&_x_tr_hl=en`;
          for (const px of [
            u => "https://api.codetabs.com/v1/proxy?quest=" + encodeURIComponent(u),
            u => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u)
          ]) {
            try {
              const r1 = await fetch(px(gtWatch));
              const html = await r1.text();
              const m = html.match(/"baseUrl"\s*:\s*"(https:\/\/www\.youtube\.com\/api\/timedtext[^"]+)"/);
              if (m) {
                let cleanU = m[1].replace(/\\u0026/g, "&").replace(/\\\//g, "/").replace("&fmt=srv3", "");
                let gtSub = cleanU.replace("https://www.youtube.com/api/timedtext", "https://www-youtube-com.translate.goog/api/timedtext") + "&_x_tr_sl=es&_x_tr_tl=en&_x_tr_hl=en";
                const r2 = await fetch(px(gtSub));
                const subs = parseCapData(await r2.text());
                if (subs.length) return subs;
              }
            } catch(e) {}
          }
          throw new Error("gt_watch_empty");
        })(),
        // Luồng 3: Quét trang Watch và tải Timedtext qua CÙNG MỘT PROXY (codetabs / allorigins) để giữ nguyên IP
        (async () => {
          const wUrl = `https://www.youtube.com/watch?v=${vid}&hl=en`;
          for (const px of [
            u => "https://api.codetabs.com/v1/proxy?quest=" + encodeURIComponent(u),
            u => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u)
          ]) {
            try {
              const r1 = await fetch(px(wUrl));
              const html = await r1.text();
              const m = html.match(/"baseUrl"\s*:\s*"(https:\/\/www\.youtube\.com\/api\/timedtext[^"]+)"/);
              if (m) {
                const cleanU = m[1].replace(/\\u0026/g, "&").replace(/\\\//g, "/").replace("&fmt=srv3", "");
                const r2 = await fetch(px(cleanU));
                const subs = parseCapData(await r2.text());
                if (subs.length) return subs;
              }
            } catch(e) {}
          }
          throw new Error("watch_px_empty");
        })(),
        // Luồng 4: Gọi NoteGPT qua Proxy
        (async () => {
          const noteUrl = "https://notegpt.io/api/v2/video-transcript?platform=youtube&video_id=" + vid;
          const r = await fetch("https://api.codetabs.com/v1/proxy?quest=" + encodeURIComponent(noteUrl));
          if (!r.ok) throw new Error("notegpt");
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
          throw new Error("empty");
        })()
      ];
      try {
        return await Promise.any(tasks);
      } catch(e) {
        return [];
      }
    }

    // CHỨC NĂNG CHIẾU MÀN HÌNH & THU ÂM PHÒNG HỌP (ZOOM / MEET / TEAMS) TRÊN PC
    async function startScreenCaptureStream() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
        return alert("Tính năng chiếu cửa sổ phòng họp hỗ trợ trên máy tính (Chrome/Edge). Trên điện thoại, bạn hãy bấm nút '1. Mở Phòng Họp' và bật '3. Bật AI Nghe & Dịch Trực Tiếp' nhé!");
      }
      try {
        const stream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: true});
        const vEl = document.getElementById("html5Player");
        vEl.srcObject = stream;
        vEl.muted = false;
        vEl.classList.remove("hidden");
        document.getElementById("ytWrapper").classList.add("hidden");
        document.getElementById("liveMeetingOverlay").classList.add("hidden");
        vEl.play();
        liveStartTimestamp = Date.now();
        if (!isLiveListening) {
          toggleLiveMicTranscribe();
        }
      } catch (e) {
        alert("Chưa chọn cửa sổ chia sẻ: " + e.message);
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
        streamChunkStartSec = Math.max(0, getCurrentPlaybackSecond() - 0.5);
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
        return alert("Trình duyệt này chưa hỗ trợ Web Speech API (Hãy mở bằng Safari trên iOS hoặc Chrome/Edge).");
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
      if (!liveStartTimestamp) {
        liveStartTimestamp = Date.now();
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

      if (currentSourceType === "youtube" && player && player.playVideo) {
        player.playVideo();
      }

      speechRec.onresult = (event) => {
        const idx = event.resultIndex;
        if (idx !== streamResultIndex) {
          streamResultIndex = idx;
          streamConsumedWords = 0;
          streamChunkStartSec = Math.max(0, getCurrentPlaybackSecond() - 1);
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
          streamChunkStartSec = Math.max(0, getCurrentPlaybackSecond() - 1);
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
          }, 300);
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

    function showPlayerMode(mode, info = {}) {
      currentSourceType = mode;
      const ytWrap = document.getElementById("ytWrapper");
      const h5 = document.getElementById("html5Player");
      const liveOv = document.getElementById("liveMeetingOverlay");

      ytWrap.classList.add("hidden");
      h5.classList.add("hidden");
      liveOv.classList.add("hidden");

      if (mode === "youtube") {
        ytWrap.classList.remove("hidden");
        initPlayer(info.video_id);
      } else if (mode === "direct_media") {
        h5.srcObject = null;
        h5.src = info.media_url;
        h5.classList.remove("hidden");
      } else {
        liveOv.classList.remove("hidden");
        document.getElementById("livePlatformTitle").innerText = info.platform || info.title || "Phòng Họp / Livestream Trực Tuyến";
        document.getElementById("livePlatformUrl").innerText = info.media_url || "";
        document.getElementById("btnOpenExternalMeeting").href = info.media_url || "#";
        liveStartTimestamp = Date.now();
      }
    }

    function applyLoadedLecture(d) {
      currentVideoId = d.video_id;
      currentTitle = d.title || ("Bài giảng (" + d.video_id + ")");
      document.getElementById("lectureTitleDisplay").innerText = currentTitle;
      document.getElementById("lectureUrlDisplay").innerText = d.media_url || ("https://www.youtube.com/watch?v=" + currentVideoId);
      subtitles = d.subtitles || [];
      lastSpokenIdx = -1;
      document.getElementById("pasteRawArea").value = subtitles.map(s => `${formatTime(s.start)} ${s.text}`).join("\n");
      renderSubs();
      startBgTrans();
    }

    // XỬ LÝ MỌI ĐƯỜNG DẪN (YOUTUBE, LINK RÚT GỌN Z-WAKA, ZOOM, GOOGLE MEET, MS TEAMS...)
    async function processUrlOrId(rawUrl) {
      const cleanUrl = (rawUrl || "").trim();
      if (!cleanUrl) return alert("Bạn hãy dán đường dẫn Video hoặc link Zoom / Meet / Teams / Z-Waka vào ô URL nhé!");

      clearAllSubtitles();
      const customT = document.getElementById("customTitle").value.trim();
      const targetLang = document.getElementById("targetLang").value;

      document.getElementById("lectureTitleDisplay").innerText = "Đang phân tích đường dẫn...";
      document.getElementById("lectureUrlDisplay").innerText = cleanUrl;
      const box = document.getElementById("subtitleList");
      box.innerHTML = `<p class="text-blue-400 text-xs sm:text-sm text-center pt-16 animate-pulse">⏳ Đang giải mã đường dẫn & trích xuất phụ đề...</p>`;
      document.getElementById("subCount").innerText = "Đang tải...";

      const r = await fetch("/api/process-video", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({url: cleanUrl, custom_title: customT, target_lang: targetLang})
      });
      const d = await r.json();

      if (d.error) {
        box.innerHTML = `<p class="text-amber-400 text-xs text-center pt-12 px-4">${d.error}</p>`;
        return;
      }

      if (d.title) {
        currentTitle = d.title;
        document.getElementById("lectureTitleDisplay").innerText = currentTitle;
      }
      if (d.video_id) currentVideoId = d.video_id;

      showPlayerMode(d.source_type || "youtube", d);

      // Nếu là phòng họp trực tiếp (Zoom / Meet / Teams / Z-Waka)
      if (d.source_type === "external_live" || d.source_type === "direct_media") {
        if (d.subtitles && d.subtitles.length) {
          applyLoadedLecture(d);
        } else {
          box.innerHTML = `<div class="text-center pt-8 px-4 space-y-3">
            <p class="text-emerald-400 font-semibold text-xs sm:text-sm">📡 Đã kết nối chế độ Phòng Họp / Livestream: ${d.title}</p>
            <p class="text-slate-300 text-xs leading-relaxed">Hãy bấm nút bên dưới để AI tự động nghe lời diễn giả và dịch song ngữ theo thời gian thực:</p>
            <div class="flex justify-center gap-2 pt-2">
              <button onclick="if(!isLiveListening) toggleLiveMicTranscribe();" class="bg-amber-600 hover:bg-amber-500 text-white text-xs px-4 py-2.5 rounded-xl font-bold shadow-lg">🎙️ Bật AI Nghe & Dịch Trực Tiếp</button>
              <button onclick="openPasteModal()" class="bg-purple-600 hover:bg-purple-500 text-white text-xs px-3.5 py-2.5 rounded-xl font-bold">📋 Dán Transcript</button>
            </div>
          </div>`;
          document.getElementById("subCount").innerText = "Chế độ Live";
        }
        return;
      }

      // Nếu là YouTube và Backend đã lấy được phụ đề
      if (d.subtitles && d.subtitles.length) {
        applyLoadedLecture(d);
        return;
      }

      // Nếu IP đám mây Render bị chặn, trình duyệt Điện thoại 4G / PC tự lấy trực tiếp
      const rawSubs = await fetchFromPhoneBrowser(d.video_id, d.pb_asr, d.pb_man, d.pb_asr_raw);
      if (rawSubs && rawSubs.length) {
        subtitles = rawSubs.map(s => ({...s, vi: s.vi || "⏳ Đang dịch...", translated: Boolean(s.translated)}));
        document.getElementById("pasteRawArea").value = subtitles.map(s => `${formatTime(s.start)} ${s.text}`).join("\n");
        renderSubs();
        startBgTrans();
        return;
      }

      box.innerHTML = `<div class="text-center pt-8 px-4 space-y-3">
        <p class="text-amber-400 text-xs sm:text-sm leading-relaxed">Video này hiện không mở phụ đề công khai. Bạn hãy bấm nút bên dưới để AI nghe và dịch trực tiếp theo thời gian thực nhé!</p>
        <div class="flex justify-center gap-2 pt-2">
          <button onclick="if(!isLiveListening) toggleLiveMicTranscribe();" class="bg-amber-600 hover:bg-amber-500 text-white text-xs px-4 py-2 rounded-lg font-bold shadow-lg">🎙️ Bật AI Nghe Loa (Dịch Tức Thì)</button>
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
        source_type: currentSourceType,
        media_url: document.getElementById("lectureUrlDisplay").innerText,
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
      showPlayerMode(item.source_type || "youtube", item);
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
      const lines = [`SỔ TỪ VỰNG BÀI GIẢNG: ${currentTitle}\n====================================\n`];
      extractedVocabList.forEach(item => {
        lines.push(`- ${item.word.toUpperCase()}: ${item.meaning} (${formatTime(item.firstStart)})`);
      });
      const blob = new Blob([lines.join("\n")], {type: "text/plain;charset=utf-8"});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `Tu_Vung_${currentVideoId || 'video'}.txt`;
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
                if (playingId && playingId !== currentVideoId && currentSourceType === "youtube") {
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
      if (!cabinEnabled || isSpeaking || currentSourceType !== "youtube" || !player || !player.getPlayerState || player.getPlayerState() !== 1) return;
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
      if (!url) return alert("Bạn hãy dán đường dẫn (YouTube, Zoom, Meet, Teams, Z-Waka...) vào ô URL nhé!");
      const btn = document.getElementById("btnLoad");
      const oldTxt = btn.innerText;
      btn.innerText = "⏳ Đang kết nối..."; btn.disabled = true; stopCabin();
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
      if (currentSourceType === "youtube" && player && player.seekTo) {
        player.setVolume(100); player.seekTo(start, true); player.playVideo();
        if (activeTimer) clearTimeout(activeTimer);
        activeTimer = setTimeout(() => player.pauseVideo(), (dur + 0.25) * 1000);
      } else if (currentSourceType === "direct_media") {
        const vEl = document.getElementById("html5Player");
        vEl.currentTime = start;
        vEl.play();
      }
    }

    function playCabinOne(i) {
      const s = subtitles[i]; if (!s) return;
      activeSubIndex = i; if (activeTimer) clearTimeout(activeTimer);
      if (currentSourceType === "youtube" && player && player.seekTo) {
        player.setVolume(duckVol); player.seekTo(s.start, true); player.playVideo();
      }
      document.getElementById("cabinLiveBar").classList.remove("hidden");
      speakText(s.vi, () => { if (!cabinEnabled && player && player.setVolume) player.setVolume(100); });
      activeTimer = setTimeout(() => { if (!cabinEnabled && currentSourceType === "youtube" && player) { player.pauseVideo(); player.setVolume(100); } }, Math.max(s.duration + 0.8, 3.5) * 1000);
    }

    function playCurrentSentence() { const s = subtitles[activeSubIndex]; if (s) playSeg(s.start, s.duration, activeSubIndex); }

    let lastHl = -1;
    setInterval(() => {
      if (isLiveListening) return;
      const t = getCurrentPlaybackSecond();
      if (!t && currentSourceType !== "youtube") return;
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
