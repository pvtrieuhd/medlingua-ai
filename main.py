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
                txt = clean_txt("".join(s.get("utf8", "") for s in
