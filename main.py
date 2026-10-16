import re
import json
import time
import urllib3
import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi

# Tắt cảnh báo SSL khi fallback trên một số môi trường mạng
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

session = requests.Session()
session.headers.update(HEADERS)

# Bản đồ sửa lỗi phụ đề tự động YouTube nghe nhầm thuật ngữ Y khoa
SPEECH_TO_TEXT_FIX = {
    "secum": "cecum",
    "seekum": "cecum",
    "seecum": "cecum",
    "caecum": "cecum",
    "oesophagus": "esophagus",
    "oesophageal": "esophageal",
    "conex": "conecct",
}

# Từ điển Y khoa chuyên ngành Nội soi Tiêu hóa (Thực quản - Dạ dày - Đại trực tràng)
MEDICAL_DICT = {
    "small": "Nhỏ", "pale": "Nhợt nhạt / Sáng màu", "round": "Tròn",
    "pit": "Hố tuyến (Pit)", "pits": "Các hố tuyến (Pits)",
    "polyp": "Polyp", "polyps": "Các khối polyp",
    "colon": "Đại tràng", "rectum": "Trực tràng", "sigmoid": "Đại tràng sigma",
    "cecum": "Manh tràng (Phần đầu đại tràng)", "secum": "Manh tràng (Cecum)",
    "ileum": "Hồi tràng", "duodenum": "Tá tràng", "gastric": "Thuộc dạ dày", "stomach": "Dạ dày",
    "esophagus": "Thực quản", "esophageal": "Thuộc thực quản",
    "squamous": "Tế bào vảy / Biểu mô vảy", "cell": "Tế bào", "cells": "Các tế bào",
    "cancer": "Ung thư", "carcinoma": "Ung thư biểu mô",
    "adenocarcinoma": "Ung thư biểu mô tuyến", "escc": "Ung thư biểu mô tế bào vảy thực quản (ESCC)",
    "mucosal": "Thuộc niêm mạc", "mucosa": "Niêm mạc",
    "submucosal": "Dưới niêm mạc", "submucosa": "Lớp dưới niêm mạc",
    "epithelium": "Biểu mô", "epithelial": "Thuộc biểu mô",
    "lamina propria": "Lớp đệm niêm mạc", "muscularis": "Lớp cơ",
    "gland": "Tuyến", "glands": "Các tuyến",
    "hyperplastic": "Tăng sản", "malignant": "Ác tính", "benign": "Lành tính",
    "dysplasia": "Loạn sản", "neoplasia": "Tân sinh / U tân sinh", "neoplasm": "Khối tân sinh",
    "invasion": "Xâm lấn", "invasive": "Có tính xâm lấn", "superficial": "Nông / Bề mặt",
    "metastasis": "Di căn", "lymph": "Bạch huyết", "lymphovascular": "Mạch bạch huyết",
    "risk": "Nguy cơ", "minimal": "Tối thiểu",
    "leave it": "Giữ lại (Không can thiệp)", "take it": "Cắt bỏ / Lấy ra",
    "endoscopy": "Nội soi", "endoscopic": "Qua nội soi", "endoscopist": "Bác sĩ nội soi",
    "lesion": "Tổn thương", "lesions": "Các tổn thương",
    "biopsy": "Sinh thiết", "adenoma": "U tuyến", "adenomas": "Các u tuyến",
    "colorectal": "Đại trực tràng", "surgery": "Phẫu thuật", "table": "Bảng phân loại / Sơ đồ",
    "close view": "Góc nhìn cận cảnh", "chromoendoscopy": "Nội soi nhuộm màu",
    "nbi": "Nội soi dải tần ánh sáng hẹp (NBI)", "wli": "Nội soi ánh sáng trắng (WLI)",
    "lugol": "Dung dịch Lugol (Nhuộm i-ốt phát hiện tổn thương thực quản)",
    "ipcl": "Quai mao mạch nhú trong biểu mô (IPCL)",
    "barrett": "Thực quản Barrett", "stricture": "Hẹp ống tiêu hóa",
    "ulcer": "Vết loét", "erosion": "Trợt niêm mạc", "nodule": "Nốt / Cục nhỏ",
    "vessel": "Mạch máu", "vessels": "Các mạch máu", "vascular": "Mạch máu",
    "resection": "Cắt bỏ", "emr": "Cắt hớt niêm mạc qua nội soi (EMR)",
    "esd": "Cắt tách dưới niêm mạc qua nội soi (ESD)",
    "screening": "Tầm soát / Sàng lọc", "detection": "Phát hiện",
    "epidemiology": "Dịch tễ học", "diagnosis": "Chẩn đoán", "prognosis": "Tiên lượng",
    "margin": "Bờ diện cắt / Ranh giới", "demarcation": "Đường ranh giới (Demarcation line)"
}

class VideoRequest(BaseModel):
    url: str

class BatchTranslateRequest(BaseModel):
    texts: list[str]

class WordRequest(BaseModel):
    word: str
    context_sentence: str
    context_vi: str = ""

def extract_youtube_id(url: str):
    pattern = r"(?:v=|\/|youtu\.be\/|embed\/)([0-9A-Za-z_-]{11})"
    match = re.search(pattern, url)
    return match.group(1) if match else None

def safe_request(method: str, url: str, **kwargs):
    kwargs.setdefault("timeout", 10)
    try:
        return session.request(method, url, **kwargs)
    except requests.exceptions.SSLError:
        kwargs["verify"] = False
        return session.request(method, url, **kwargs)

def clean_spoken_fillers(text: str) -> str:
    """Loại bỏ các từ đệm 'uh', 'um', 'er' trong hội nghị để dịch tiếng Việt tự nhiên hơn."""
    cleaned = re.sub(r"\b(uh+|um+|er+|erm+|ah+|hmm+)\b", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else text.strip()

def raw_google_translate(text_block: str) -> str:
    if not text_block or not text_block.strip():
        return text_block

    # 1. Endpoint GTX qua POST
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "en", "tl": "vi", "dt": "t"}
        r = safe_request("POST", url, params=params, data={"q": text_block})
        if r.status_code == 200:
            data = r.json()
            if data and data[0]:
                res = "".join([part[0] for part in data[0] if part and part[0]])
                if res.strip():
                    return res.strip()
    except Exception:
        pass

    # 2. Endpoint Clients5 qua POST
    try:
        url = "https://clients5.google.com/translate_a/t"
        params = {"client": "dict-chrome-ex", "sl": "en", "tl": "vi"}
        r = safe_request("POST", url, params=params, data={"q": text_block})
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                if isinstance(data[0], str):
                    return data[0].strip()
                elif isinstance(data[0], list) and data[0][0]:
                    return data[0][0].strip()
    except Exception:
        pass

    return text_block

def translate_single_text(text: str) -> str:
    if not text or not text.strip():
        return text
    clean_t = clean_spoken_fillers(text.strip())
    lower_t = clean_t.lower().rstrip(".")
    if lower_t in SPEECH_TO_TEXT_FIX:
        lower_t = SPEECH_TO_TEXT_FIX[lower_t]
    if lower_t in MEDICAL_DICT:
        return MEDICAL_DICT[lower_t]

    res = raw_google_translate(clean_t)
    if res and res.lower() != clean_t.lower():
        return res

    if len(clean_t) < 450:
        try:
            r = safe_request("GET", "https://api.mymemory.translated.net/get", params={"q": clean_t, "langpair": "en|vi"})
            if r.status_code == 200:
                mm = r.json().get("responseData", {}).get("translatedText", "")
                if mm:
                    return mm.strip()
        except Exception:
            pass
    return text

def translate_indexed_chunk(chunk_texts: list[str]) -> list[str]:
    """Dịch gộp 1 lô câu bằng nhãn [0], [1], [2]... tránh gộp dòng."""
    if not chunk_texts:
        return []

    formatted_lines = []
    for idx, t in enumerate(chunk_texts):
        cleaned = clean_spoken_fillers(t)
        if not cleaned:
            cleaned = "..."
        if cleaned[-1] not in ".?!":
            cleaned_with_dot = cleaned + "."
        else:
            cleaned_with_dot = cleaned
        formatted_lines.append(f"[{idx}] {cleaned_with_dot}")

    payload = "\n".join(formatted_lines)
    translated_block = raw_google_translate(payload)

    matches = re.findall(r"\[\s*(\d+)\s*\]\s*(.*?)(?=\[\s*\d+\s*\]|$)", translated_block, flags=re.DOTALL)
    result_map = {}
    for idx_str, trans_val in matches:
        i = int(idx_str)
        val = trans_val.strip()
        if i < len(chunk_texts) and not chunk_texts[i].strip().endswith("."):
            val = val.rstrip(".")
        result_map[i] = val.strip()

    final_results = []
    for i, orig in enumerate(chunk_texts):
        lower_orig = orig.lower().strip().rstrip(".")
        if lower_orig in MEDICAL_DICT:
            final_results.append(MEDICAL_DICT[lower_orig])
            continue

        trans = result_map.get(i, "")
        if not trans or trans.lower() == clean_spoken_fillers(orig).lower():
            trans = translate_single_text(orig)
        final_results.append(trans if trans else orig)

    return final_results

def translate_list_in_batches(texts: list[str], batch_size: int = 35) -> list[str]:
    all_translated = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        all_translated.extend(translate_indexed_chunk(chunk))
        if i + batch_size < len(texts):
            time.sleep(0.1)
    return all_translated

def fetch_youtube_subtitles(video_id: str):
    langs = ['en', 'en-US', 'en-GB']
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        return YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
    else:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=langs)
        return [{"text": s.text, "start": s.start, "duration": s.duration} for s in fetched]

@app.get("/api/tts")
async def text_to_speech(text: str = Query(..., max_length=500)):
    """API tạo giọng đọc Thuyết minh Cabin Tiếng Việt mượt mà từ Google TTS."""
    clean_t = text.replace("⏳", "").strip()
    if not clean_t:
        return Response(status_code=204)

    if len(clean_t) > 200:
        clean_t = clean_t[:197] + "..."

    tts_urls = [
        "https://translate.googleapis.com/translate_tts",
        "https://translate.google.com/translate_tts"
    ]
    for url in tts_urls:
        try:
            params = {
                "ie": "UTF-8",
                "q": clean_t,
                "tl": "vi",
                "client": "tw-ob",
                "ttsspeed": "1"
            }
            r = safe_request("GET", url, params=params, timeout=6)
            if r.status_code == 200 and r.content:
                return Response(
                    content=r.content,
                    media_type="audio/mpeg",
                    headers={"Cache-Control": "public, max-age=86400"}
                )
        except Exception:
            pass

    return Response(status_code=502)

@app.post("/api/process-video")
async def process_video(req: VideoRequest):
    video_id = extract_youtube_id(req.url)
    if not video_id:
        return {"error": "Link YouTube không hợp lệ!"}

    try:
        raw_transcript = fetch_youtube_subtitles(video_id)
        subtitles = raw_transcript[:3000]

        for item in subtitles:
            clean_t = item["text"].replace("\n", " ").strip()
            item["text"] = clean_t if clean_t else "..."

        initial_count = min(100, len(subtitles))
        initial_texts = [subtitles[i]["text"] for i in range(initial_count)]
        initial_vi = translate_list_in_batches(initial_texts, batch_size=35)

        for i, item in enumerate(subtitles):
            if i < initial_count:
                item["vi"] = initial_vi[i]
                item["translated"] = True
            else:
                item["vi"] = "⏳ Đang dịch tự động..."
                item["translated"] = False

        return {"video_id": video_id, "subtitles": subtitles}
    except Exception as e:
        return {"error": f"Không thể lấy phụ đề video này. Chi tiết: {str(e)}"}

@app.post("/api/translate-batch")
async def api_translate_batch(req: BatchTranslateRequest):
    try:
        translated = translate_list_in_batches(req.texts, batch_size=40)
        return {"translations": translated}
    except Exception as e:
        return {"error": str(e), "translations": req.texts}

def get_word_candidates(word: str) -> list[str]:
    w = word.lower().strip()
    candidates = [w]
    if w in SPEECH_TO_TEXT_FIX:
        candidates.insert(0, SPEECH_TO_TEXT_FIX[w])

    base_list = list(candidates)
    for item in base_list:
        if item.endswith("ies") and len(item) > 4:
            candidates.append(item[:-3] + "y")
        if item.endswith("es") and len(item) > 3:
            candidates.append(item[:-2])
        if item.endswith("s") and not item.endswith("ss") and len(item) > 2:
            candidates.append(item[:-1])
        if item.endswith("ed") and len(item) > 3:
            candidates.extend([item[:-2], item[:-1]])
        if item.endswith("ing") and len(item) > 4:
            candidates.extend([item[:-3], item[:-3] + "e"])
    return list(dict.fromkeys(candidates))

@app.post("/api/lookup-word")
async def lookup_word(req: WordRequest):
    clean_w = req.word.strip()
    lower_w = clean_w.lower()
    candidates = get_word_candidates(clean_w)

    ipa = "..."
    pos = "Từ vựng"
    en_def = ""
    audio_url = ""
    matched_dict_word = clean_w

    for cand in candidates:
        try:
            r = safe_request("GET", f"https://api.dictionaryapi.dev/api/v2/entries/en/{cand}", timeout=4)
            if r.status_code == 200:
                d = r.json()[0]
                matched_dict_word = cand
                ipa = d.get("phonetic", "").strip("/")
                if not ipa and d.get("phonetics"):
                    for p in d["phonetics"]:
                        if p.get("text"):
                            ipa = p["text"].strip("/")
                            break
                for p in d.get("phonetics", []):
                    if p.get("audio"):
                        audio_url = p["audio"]
                        if "-us.mp3" in audio_url:
                            break
                if d.get("meanings"):
                    pos = d["meanings"][0].get("partOfSpeech", "Từ vựng")
                    en_def = d["meanings"][0]["definitions"][0].get("definition", "")
                break
        except Exception:
            pass

    med_meaning = None
    for cand in candidates:
        if cand in MEDICAL_DICT:
            med_meaning = MEDICAL_DICT[cand]
            pos = "Thuật ngữ Y khoa" if pos == "Từ vựng" else f"{pos} • Y khoa"
            break

    lookup_target = SPEECH_TO_TEXT_FIX.get(lower_w, clean_w)
    general_meaning = translate_single_text(lookup_target)

    if med_meaning:
        if general_meaning.lower() != lookup_target.lower() and general_meaning.lower() not in med_meaning.lower():
            vi_word = f"{med_meaning} ({general_meaning})"
        else:
            vi_word = med_meaning
    else:
        vi_word = general_meaning

    vi_sentence = req.context_vi if (req.context_vi and "⏳" not in req.context_vi) else ""
    if not vi_sentence or vi_sentence.lower() == req.context_sentence.lower():
        vi_sentence = translate_single_text(req.context_sentence)

    explain_parts = []
    if lower_w in SPEECH_TO_TEXT_FIX:
        explain_parts.append(f"• Lưu ý: Phụ đề tự động ghi \"{clean_w}\" (viết chuẩn Y khoa là \"{SPEECH_TO_TEXT_FIX[lower_w]}\").")
    if en_def:
        explain_parts.append(f"• Định nghĩa gốc: {en_def}")
    explain_parts.append(f"• Dịch câu: \"{vi_sentence}\"")

    display_word = f"{clean_w} ({matched_dict_word})" if lower_w in SPEECH_TO_TEXT_FIX else clean_w

    return {
        "word": display_word,
        "ipa": ipa or "...",
        "pos": pos,
        "meaning": vi_word,
        "explanation": "\n".join(explain_parts),
        "audio": audio_url
    }

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <meta name="theme-color" content="#020617">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <title>MedLingua AI - Học Tiếng Anh & Dịch Cabin</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    * { -webkit-tap-highlight-color: transparent; }
    body { touch-action: manipulation; }
    .word-span {
      cursor: pointer;
      padding: 2px 4px;
      margin: 0 -1px;
      border-radius: 5px;
      transition: all 0.15s;
      display: inline-block;
    }
    .word-span:hover, .word-span:active {
      background-color: #fde047;
      color: #000;
      font-weight: 600;
    }
    .active-sub {
      background-color: #1e3a8a !important;
      border-left: 4px solid #38bdf8 !important;
    }
    .cabin-glow {
      box-shadow: 0 0 18px rgba(16, 185, 129, 0.35);
      border-color: #10b981 !important;
    }
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: #0f172a; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen pb-6 lg:p-6">
  <div class="max-w-7xl mx-auto">

    <!-- HEADER & Ô DÁN LINK (Gọn gàng trên điện thoại) -->
    <div class="p-3 sm:p-4 lg:p-0">
      <header class="border-b border-slate-800 pb-3 mb-3 flex flex-wrap justify-between items-center gap-2">
        <div>
          <h1 class="text-lg sm:text-2xl font-bold text-blue-400 flex items-center gap-2">
            🎬 MedLingua AI
            <span class="text-[11px] sm:text-xs bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded-full font-normal">
              Dịch Cabin • Mobile & PC
            </span>
          </h1>
          <p class="text-xs sm:text-sm text-slate-400 mt-0.5 hidden sm:block">
            Vừa nghe tiếng Anh gốc vừa nghe thuyết minh Tiếng Việt • Chạm từng từ để tra nghĩa Y khoa
          </p>
        </div>
      </header>

      <div class="bg-slate-900 border border-slate-800 p-2.5 sm:p-4 rounded-xl mb-2 lg:mb-5">
        <div class="flex flex-row gap-2">
          <input id="videoUrl" type="text" placeholder="Dán link YouTube vào đây..." 
                 class="flex-1 p-2.5 sm:p-3 rounded-lg bg-slate-950 border border-slate-700 text-white focus:outline-none focus:border-blue-500 text-xs sm:text-sm min-w-0">
          <button onclick="analyzeVideo()" id="btnLoad"
                  class="bg-blue-600 hover:bg-blue-500 active:bg-blue-700 px-3.5 sm:px-6 py-2.5 sm:py-3 rounded-lg font-semibold text-xs sm:text-sm transition shrink-0">
            ⚡ Dịch Video
          </button>
        </div>
      </div>
    </div>

    <!-- BỐ CỤC CHÍNH: GHIM VIDEO TRÊN ĐIỆN THOẠI + CHIA CỘT TRÊN MÁY TÍNH -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-3 lg:gap-6 px-2 sm:px-4 lg:px-0">

      <!-- CỘT TRÁI: VIDEO + DỊCH CABIN (Sticky trên Mobile để lướt phụ đề vẫn thấy Video) -->
      <div class="lg:col-span-2 space-y-2.5 sm:space-y-4 sticky top-0 z-30 bg-slate-950 pt-1 pb-2 lg:static lg:p-0">
        <div class="aspect-video bg-black rounded-xl overflow-hidden border border-slate-800 shadow-2xl relative">
          <div id="player" class="w-full h-full"></div>
        </div>

        <!-- BẢNG ĐIỀU KHIỂN DỊCH CABIN -->
        <div id="cabinPanel" class="p-2.5 sm:p-4 rounded-xl bg-slate-900 border border-slate-800 transition-all">
          <div class="flex flex-wrap items-center justify-between gap-2 sm:gap-3">
            <button id="btnCabinToggle" onclick="toggleCabinMode()"
                    class="w-full sm:w-auto justify-center bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-3.5 py-2 sm:py-2.5 rounded-xl font-bold text-xs sm:text-sm flex items-center gap-2 transition">
              <span id="cabinDot" class="w-2.5 h-2.5 rounded-full bg-slate-500 shrink-0"></span>
              <span id="cabinBtnText">🎧 Bật Dịch Cabin (Nghe Song Song)</span>
            </button>

            <div class="flex items-center justify-between w-full sm:w-auto gap-2 sm:gap-3 text-xs text-slate-300">
              <div class="flex items-center gap-1.5 bg-slate-950 px-2.5 py-1.5 rounded-lg border border-slate-800 flex-1 sm:flex-initial justify-center">
                <span class="text-[11px] sm:text-xs">🔊 Âm gốc:</span>
                <input id="origVolSlider" type="range" min="5" max="70" value="20" 
                       oninput="updateOrigVolLabel(this.value)" class="w-14 sm:w-20 accent-blue-500 cursor-pointer">
                <span id="origVolValue" class="font-mono text-blue-400 font-bold text-[11px] sm:text-xs w-7">20%</span>
              </div>

              <div class="flex items-center gap-1 bg-slate-950 px-2.5 py-1.5 rounded-lg border border-slate-800">
                <span class="text-[11px] sm:text-xs">🎙️ Tốc độ:</span>
                <select id="cabinSpeed" onchange="updateCabinSpeed(this.value)" 
                        class="bg-slate-900 text-emerald-400 font-bold text-[11px] sm:text-xs focus:outline-none cursor-pointer">
                  <option value="1.05">1.0x</option>
                  <option value="1.2" selected>1.2x</option>
                  <option value="1.35">1.35x</option>
                  <option value="1.5">1.5x</option>
                </select>
              </div>
            </div>
          </div>

          <!-- Thanh trạng thái đọc Cabin -->
          <div id="cabinLiveBar" class="hidden mt-2 pt-2 border-t border-slate-800/80 flex items-center gap-2 text-xs sm:text-sm">
            <span class="text-[10px] sm:text-xs bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded font-semibold shrink-0 animate-pulse">
              🎙️ Cabin
            </span>
            <p id="cabinLiveText" class="text-emerald-400 font-medium truncate">...</p>
          </div>
        </div>

        <!-- HỘP TRA TỪ ĐIỂN Y KHOA (Trên điện thoại biến thành Bottom Drawer trượt lên; Trên PC nằm cố định) -->
        <div id="wordBox" class="hidden fixed bottom-0 left-0 right-0 z-50 p-4 rounded-t-2xl bg-slate-900/95 backdrop-blur-md border-t-2 border-blue-500 shadow-[0_-10px_30px_rgba(0,0,0,0.8)] lg:static lg:z-auto lg:p-5 lg:rounded-xl lg:border lg:border-slate-800 lg:shadow-xl">
          <div class="flex items-start justify-between gap-2 border-b border-slate-800 pb-2.5 mb-2.5">
            <div>
              <div class="flex items-center gap-2 flex-wrap">
                <span id="wText" class="text-xl sm:text-2xl font-bold text-yellow-400"></span>
                <span id="wIpa" class="text-slate-400 font-mono text-xs sm:text-sm"></span>
                <span id="wPos" class="text-[11px] sm:text-xs bg-slate-800 text-blue-300 px-2 py-0.5 rounded-full"></span>
              </div>
              <p id="wMeaning" class="text-lg sm:text-xl text-emerald-400 font-bold mt-1"></p>
            </div>

            <button onclick="closeWordBox()" class="text-slate-400 hover:text-white bg-slate-800 hover:bg-slate-700 w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold shrink-0">
              ✕
            </button>
          </div>

          <p id="wExplain" class="text-xs sm:text-sm text-slate-300 leading-relaxed whitespace-pre-line max-h-32 overflow-y-auto mb-3"></p>

          <div class="flex gap-2">
            <button id="btnDictAudio" onclick="playDictMp3()" class="hidden flex-1 sm:flex-initial justify-center bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 px-3 py-2 rounded-lg text-xs font-semibold transition flex items-center gap-1">
              🎙️ Phát âm từ điển
            </button>
            <button onclick="playCurrentSentence()" class="flex-1 sm:flex-initial justify-center bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg text-xs font-semibold transition flex items-center gap-1">
              🔊 Nghe lại câu gốc
            </button>
          </div>
        </div>
      </div>

      <!-- CỘT PHẢI: DANH SÁCH PHỤ ĐỀ TƯƠNG TÁC -->
      <div class="bg-slate-900 rounded-xl border border-slate-800 p-3 sm:p-4 h-[52vh] sm:h-[600px] lg:h-[650px] flex flex-col shadow-xl">
        <div class="border-b border-slate-800 pb-2.5 mb-2.5 flex justify-between items-center gap-2">
          <div>
            <h2 class="font-bold text-slate-200 text-xs sm:text-sm">PHỤ ĐỀ SONG NGỮ TƯƠNG TÁC</h2>
            <p class="text-[11px] sm:text-xs text-slate-400 mt-0.5">Chạm từ để tra nghĩa • Chạm 🎙️ để nghe Cabin</p>
          </div>
          <span id="subCount" class="text-[11px] sm:text-xs bg-slate-800 text-emerald-400 px-2.5 py-1 rounded font-medium shrink-0"></span>
        </div>
        <div id="subtitleList" class="flex-1 overflow-y-auto space-y-2 pr-1 relative">
          <p class="text-slate-400 text-xs sm:text-sm text-center pt-16">Đang tải dữ liệu phụ đề song ngữ...</p>
        </div>
      </div>

    </div>
  </div>

  <script src="https://www.youtube.com/iframe_api"></script>
  <script>
    let player;
    let subtitles = [];
    let activeTimer = null;
    let activeSubIndex = 0;
    let currentDictAudio = "";
    let translationJobId = 0;

    // Dùng 1 đối tượng Audio duy nhất để tương thích chính sách Autoplay của iOS Safari & Android Chrome
    let cabinEnabled = false;
    const cabinAudio = new Audio();
    cabinAudio.playsInline = true;
    const dictAudioPlayer = new Audio();
    dictAudioPlayer.playsInline = true;

    let isCabinSpeaking = false;
    let lastCabinSpokenIdx = -1;
    let origDuckVolume = 20;
    let cabinSpeechRate = 1.2;

    function onYouTubeIframeAPIReady() {
      loadVideoDirect("gedoSfZvBgE");
    }

    async function loadVideoDirect(videoId) {
      const res = await fetch("/api/process-video", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: "https://www.youtube.com/watch?v=" + videoId })
      });
      const data = await res.json();
      if (!data.error) {
        subtitles = data.subtitles;
        lastCabinSpokenIdx = -1;
        initPlayer(data.video_id);
        renderSubtitles();
        startBackgroundTranslation();
      }
    }

    function initPlayer(videoId) {
      if (player && player.loadVideoById) {
        player.loadVideoById(videoId);
      } else {
        player = new YT.Player('player', {
          height: '100%',
          width: '100%',
          videoId: videoId,
          playerVars: { 'playsinline': 1, 'rel': 0 },
          events: {
            'onStateChange': onPlayerStateChange
          }
        });
      }
    }

    function onPlayerStateChange(event) {
      if (event.data === YT.PlayerState.PAUSED || event.data === YT.PlayerState.ENDED) {
        stopCabinAudio();
      }
    }

    function updateOrigVolLabel(val) {
      origDuckVolume = parseInt(val, 10);
      document.getElementById("origVolValue").innerText = `${origDuckVolume}%`;
      if (cabinEnabled && player && player.setVolume) {
        player.setVolume(isCabinSpeaking ? origDuckVolume : Math.min(origDuckVolume + 25, 80));
      }
    }

    function updateCabinSpeed(val) {
      cabinSpeechRate = parseFloat(val);
      cabinAudio.playbackRate = cabinSpeechRate;
    }

    function unlockMobileAudio() {
      // Mở khóa quyền phát âm thanh trên iPhone/Android ngay khi người dùng chạm nút
      cabinAudio.src = "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAAABkYXRhAgAAAAEA";
      cabinAudio.play().then(() => {
        cabinAudio.pause();
        cabinAudio.currentTime = 0;
      }).catch(() => {});
    }

    function toggleCabinMode() {
      cabinEnabled = !cabinEnabled;
      const btn = document.getElementById("btnCabinToggle");
      const dot = document.getElementById("cabinDot");
      const text = document.getElementById("cabinBtnText");
      const panel = document.getElementById("cabinPanel");
      const liveBar = document.getElementById("cabinLiveBar");

      if (cabinEnabled) {
        unlockMobileAudio();
        btn.className = "w-full sm:w-auto justify-center bg-emerald-600 hover:bg-emerald-500 text-white border border-emerald-400 px-3.5 py-2 sm:py-2.5 rounded-xl font-bold text-xs sm:text-sm flex items-center gap-2 transition shadow-lg";
        dot.className = "w-2.5 h-2.5 rounded-full bg-white animate-ping shrink-0";
        text.innerText = "🎧 Đang Bật Dịch Cabin (Chạm để tắt)";
        panel.classList.add("cabin-glow");
        liveBar.classList.remove("hidden");

        lastCabinSpokenIdx = activeSubIndex - 1;
        if (player && player.setVolume) {
          player.setVolume(origDuckVolume);
        }
        triggerCabinIfNeeded();
      } else {
        btn.className = "w-full sm:w-auto justify-center bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-3.5 py-2 sm:py-2.5 rounded-xl font-bold text-xs sm:text-sm flex items-center gap-2 transition";
        dot.className = "w-2.5 h-2.5 rounded-full bg-slate-500 shrink-0";
        text.innerText = "🎧 Bật Dịch Cabin (Nghe Song Song)";
        panel.classList.remove("cabin-glow");
        liveBar.classList.add("hidden");

        stopCabinAudio();
        if (player && player.setVolume) {
          player.setVolume(100);
        }
      }
    }

    function stopCabinAudio() {
      try {
        cabinAudio.pause();
      } catch (e) {}
      if (window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
      isCabinSpeaking = false;
    }

    function speakCabinText(viText, onDoneCallback) {
      if (!viText || viText.includes("⏳")) {
        if (onDoneCallback) onDoneCallback();
        return;
      }

      stopCabinAudio();
      isCabinSpeaking = true;
      document.getElementById("cabinLiveText").innerText = viText;

      if (player && player.setVolume) {
        player.setVolume(origDuckVolume);
      }

      cabinAudio.src = `/api/tts?text=${encodeURIComponent(viText)}`;
      cabinAudio.playbackRate = cabinSpeechRate;

      cabinAudio.onended = () => {
        isCabinSpeaking = false;
        if (cabinEnabled && player && player.setVolume) {
          player.setVolume(Math.min(origDuckVolume + 20, 75));
        }
        if (onDoneCallback) onDoneCallback();
      };

      cabinAudio.onerror = () => {
        if ('speechSynthesis' in window) {
          const utter = new SpeechSynthesisUtterance(viText);
          utter.lang = 'vi-VN';
          utter.rate = cabinSpeechRate;
          utter.onend = () => {
            isCabinSpeaking = false;
            if (onDoneCallback) onDoneCallback();
          };
          utter.onerror = () => {
            isCabinSpeaking = false;
            if (onDoneCallback) onDoneCallback();
          };
          window.speechSynthesis.speak(utter);
        } else {
          isCabinSpeaking = false;
          if (onDoneCallback) onDoneCallback();
        }
      };

      cabinAudio.play().catch(() => {
        isCabinSpeaking = false;
        if (onDoneCallback) onDoneCallback();
      });
    }

    function triggerCabinIfNeeded() {
      if (!cabinEnabled || isCabinSpeaking) return;
      if (!player || !player.getPlayerState || player.getPlayerState() !== YT.PlayerState.PLAYING) return;

      if (activeSubIndex > lastCabinSpokenIdx && subtitles[activeSubIndex]) {
        let textToRead = "";
        let targetIdx = activeSubIndex;

        if (activeSubIndex - lastCabinSpokenIdx === 2 && subtitles[activeSubIndex - 1]) {
          const prevVi = subtitles[activeSubIndex - 1].vi || "";
          const currVi = subtitles[activeSubIndex].vi || "";
          if (!prevVi.includes("⏳") && !currVi.includes("⏳") && (prevVi.length + currVi.length < 160)) {
            textToRead = `${prevVi}, ${currVi}`;
          } else {
            textToRead = currVi;
          }
        } else {
          textToRead = subtitles[activeSubIndex].vi || "";
        }

        if (
          textToRead.length < 45 &&
          targetIdx + 1 < subtitles.length &&
          subtitles[targetIdx + 1].translated &&
          (subtitles[targetIdx + 1].start - (subtitles[targetIdx].start + subtitles[targetIdx].duration)) < 1.0
        ) {
          textToRead = `${textToRead} ${subtitles[targetIdx + 1].vi}`;
          targetIdx = targetIdx + 1;
        }

        lastCabinSpokenIdx = targetIdx;
        speakCabinText(textToRead, () => {
          if (cabinEnabled) {
            setTimeout(triggerCabinIfNeeded, 80);
          }
        });
      }
    }

    async function analyzeVideo() {
      const url = document.getElementById("videoUrl").value.trim();
      if (!url) return alert("Bạn hãy dán link YouTube vào ô trống nhé!");

      const btn = document.getElementById("btnLoad");
      btn.innerText = "⏳ Đang xử lý...";
      btn.disabled = true;
      stopCabinAudio();

      try {
        const res = await fetch("/api/process-video", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url })
        });
        const data = await res.json();
        if (data.error) {
          alert(data.error);
        } else {
          subtitles = data.subtitles;
          lastCabinSpokenIdx = -1;
          initPlayer(data.video_id);
          renderSubtitles();
          startBackgroundTranslation();
        }
      } catch (err) {
        alert("Lỗi kết nối: " + err.message);
      } finally {
        btn.innerText = "⚡ Dịch Video";
        btn.disabled = false;
      }
    }

    function updateProgressBadge() {
      const total = subtitles.length;
      const done = subtitles.filter(s => s.translated).length;
      const badge = document.getElementById("subCount");
      if (done < total) {
        badge.innerText = `⚡ Đang dịch ${done}/${total}`;
      } else {
        badge.innerText = `${total} câu`;
      }
    }

    async function startBackgroundTranslation() {
      const currentJob = ++translationJobId;
      updateProgressBadge();

      const BATCH_SIZE = 40;
      while (currentJob === translationJobId) {
        let startIndex = -1;
        for (let i = activeSubIndex; i < subtitles.length; i++) {
          if (!subtitles[i].translated) {
            startIndex = i;
            break;
          }
        }
        if (startIndex === -1) {
          for (let i = 0; i < activeSubIndex; i++) {
            if (!subtitles[i].translated) {
              startIndex = i;
              break;
            }
          }
        }

        if (startIndex === -1) break;

        const batchIndices = [];
        const batchTexts = [];
        for (let i = startIndex; i < subtitles.length && batchIndices.length < BATCH_SIZE; i++) {
          if (!subtitles[i].translated) {
            batchIndices.push(i);
            batchTexts.push(subtitles[i].text);
          }
        }

        try {
          const res = await fetch("/api/translate-batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ texts: batchTexts })
          });
          const data = await res.json();
          if (currentJob !== translationJobId) return;

          if (data.translations && data.translations.length) {
            batchIndices.forEach((subIdx, k) => {
              const viText = data.translations[k] || subtitles[subIdx].text;
              subtitles[subIdx].vi = viText;
              subtitles[subIdx].translated = true;
              const viEl = document.getElementById(`sub-vi-${subIdx}`);
              if (viEl) viEl.innerText = viText;
            });
            updateProgressBadge();
          }
        } catch (err) {
          await new Promise(r => setTimeout(r, 1000));
        }
      }
    }

    function renderSubtitles() {
      const container = document.getElementById("subtitleList");
      updateProgressBadge();
      container.innerHTML = "";

      subtitles.forEach((sub, index) => {
        const div = document.createElement("div");
        div.id = `sub-${index}`;
        div.className = "p-2.5 sm:p-3 rounded-lg bg-slate-950/70 border border-slate-800/80 transition hover:bg-slate-800/60 cursor-pointer";

        const wordsHtml = sub.text.split(" ").map(w => {
          const cleanWord = w.replace(/[^a-zA-Z0-9-]/g, "");
          return `<span class="word-span" onclick="lookupWord(event, '${cleanWord}', ${index})">${w}</span>`;
        }).join(" ");

        div.innerHTML = `
          <div class="flex justify-between items-start gap-2">
            <div class="text-sm sm:text-base font-medium text-slate-100 leading-relaxed">${wordsHtml}</div>
            <div class="flex gap-1 shrink-0">
              <button onclick="event.stopPropagation(); playCabinSingleSentence(${index})" 
                      class="text-[11px] sm:text-xs bg-emerald-600/20 hover:bg-emerald-600 active:bg-emerald-700 text-emerald-300 hover:text-white px-2 py-1 rounded transition">
                🎙️ Cabin
              </button>
              <button onclick="event.stopPropagation(); playSegment(${sub.start}, ${sub.duration}, ${index})" 
                      class="text-[11px] sm:text-xs bg-slate-800 hover:bg-blue-600 active:bg-blue-700 text-slate-300 hover:text-white px-2 py-1 rounded transition">
                🔊 Gốc
              </button>
            </div>
          </div>
          <div id="sub-vi-${index}" class="text-xs sm:text-sm text-emerald-400 font-medium mt-1">${sub.vi}</div>
        `;

        div.onclick = () => {
          if (cabinEnabled) {
            playCabinFromIndex(index);
          } else {
            playSegment(sub.start, sub.duration, index);
          }
        };
        container.appendChild(div);
      });
    }

    function scrollSubtitleContainerOnly(targetEl) {
      // Cuộn mượt bên trong khung phụ đề mà KHÔNG làm nhảy trang trên điện thoại
      const container = document.getElementById("subtitleList");
      if (!container || !targetEl) return;
      const targetTop = targetEl.offsetTop - container.offsetTop - (container.clientHeight / 3);
      container.scrollTo({
        top: Math.max(0, targetTop),
        behavior: "smooth"
      });
    }

    function playSegment(start, duration, index) {
      activeSubIndex = index;
      stopCabinAudio();
      if (player && player.seekTo) {
        player.setVolume(100);
        player.seekTo(start, true);
        player.playVideo();
        if (activeTimer) clearTimeout(activeTimer);
        activeTimer = setTimeout(() => {
          player.pauseVideo();
        }, (duration + 0.25) * 1000);
      }
    }

    function playCabinSingleSentence(index) {
      const sub = subtitles[index];
      if (!sub) return;
      activeSubIndex = index;
      if (activeTimer) clearTimeout(activeTimer);

      if (player && player.seekTo) {
        player.setVolume(origDuckVolume);
        player.seekTo(sub.start, true);
        player.playVideo();
      }

      document.getElementById("cabinLiveBar").classList.remove("hidden");
      speakCabinText(sub.vi, () => {
        if (!cabinEnabled && player && player.setVolume) {
          player.setVolume(100);
        }
      });

      activeTimer = setTimeout(() => {
        if (!cabinEnabled && player && player.pauseVideo) {
          player.pauseVideo();
          player.setVolume(100);
        }
      }, Math.max(sub.duration + 0.8, 3.5) * 1000);
    }

    function playCabinFromIndex(index) {
      const sub = subtitles[index];
      if (!sub) return;
      if (activeTimer) clearTimeout(activeTimer);
      activeSubIndex = index;
      lastCabinSpokenIdx = index - 1;
      stopCabinAudio();

      if (player && player.seekTo) {
        player.setVolume(origDuckVolume);
        player.seekTo(sub.start, true);
        player.playVideo();
      }
      setTimeout(triggerCabinIfNeeded, 150);
    }

    function playCurrentSentence() {
      const sub = subtitles[activeSubIndex];
      if (sub) playSegment(sub.start, sub.duration, activeSubIndex);
    }

    function playDictMp3() {
      if (currentDictAudio) {
        dictAudioPlayer.src = currentDictAudio;
        dictAudioPlayer.play().catch(() => {});
      }
    }

    function closeWordBox() {
      document.getElementById("wordBox").classList.add("hidden");
    }

    let lastHighlightIdx = -1;
    setInterval(() => {
      if (!player || !player.getCurrentTime) return;
      const t = player.getCurrentTime();
      let foundIdx = -1;

      for (let idx = 0; idx < subtitles.length; idx++) {
        const sub = subtitles[idx];
        if (t >= sub.start && t <= (sub.start + sub.duration)) {
          foundIdx = idx;
          break;
        }
      }

      if (foundIdx !== -1 && foundIdx !== lastHighlightIdx) {
        if (lastHighlightIdx !== -1) {
          const prevEl = document.getElementById(`sub-${lastHighlightIdx}`);
          if (prevEl) prevEl.classList.remove("active-sub");
        }
        const currEl = document.getElementById(`sub-${foundIdx}`);
        if (currEl) {
          currEl.classList.add("active-sub");
          scrollSubtitleContainerOnly(currEl);
        }

        if (foundIdx < lastCabinSpokenIdx || foundIdx - lastCabinSpokenIdx > 4) {
          lastCabinSpokenIdx = foundIdx - 1;
        }

        activeSubIndex = foundIdx;
        lastHighlightIdx = foundIdx;

        if (cabinEnabled) {
          triggerCabinIfNeeded();
        }
      }
    }, 200);

    async function lookupWord(event, word, subIndex) {
      event.stopPropagation();
      if (!word) return;

      const sub = subtitles[subIndex];
      playSegment(sub.start, sub.duration, subIndex);

      const box = document.getElementById("wordBox");
      box.classList.remove("hidden");
      document.getElementById("wText").innerText = word;
      document.getElementById("wIpa").innerText = "Đang tra...";
      document.getElementById("wPos").innerText = "...";
      document.getElementById("wMeaning").innerText = "Đang dịch nghĩa...";
      document.getElementById("wExplain").innerText = "";
      document.getElementById("btnDictAudio").classList.add("hidden");

      try {
        const res = await fetch("/api/lookup-word", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            word: word,
            context_sentence: sub.text,
            context_vi: sub.vi
          })
        });
        const data = await res.json();

        document.getElementById("wText").innerText = data.word;
        document.getElementById("wIpa").innerText = `/${data.ipa}/`;
        document.getElementById("wPos").innerText = data.pos;
        document.getElementById("wMeaning").innerText = data.meaning;
        document.getElementById("wExplain").innerText = data.explanation;

        if (data.audio) {
          currentDictAudio = data.audio;
          document.getElementById("btnDictAudio").classList.remove("hidden");
        }
      } catch (e) {
        document.getElementById("wMeaning").innerText = "Lỗi kết nối khi tra từ.";
      }
    }
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_home():
    return HTMLResponse(content=HTML_CONTENT, status_code=200)
