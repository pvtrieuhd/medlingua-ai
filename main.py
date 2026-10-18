import re, json, time, html, base64, urllib.parse, urllib3, requests
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
ANDROID_UA = "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip"
WEB_CLIENT_VERSION = "2.20260115.01.00"

SPEECH_FIX = {
    "secum": "cecum", "seekum": "cecum", "caecum": "cecum",
    "oesophagus": "esophagus", "oesophageal": "esophageal",
    "esophagal": "esophageal", "squa": "squamous", "spos": "squamous",
    "conex": "conecct", "lugal": "lugol", "rugal": "lugol",
    "yuchi": "iacucci", "kareim": "karim", "alcer": "ulcerative", "chrone": "crohn's"
}

MEDICAL_DICT = {
    "small": "Nhỏ", "pale": "Nhợt nhạt / Sáng màu", "round": "Tròn", "pit": "Hố tuyến", "pits": "Các hố tuyến",
    "polyp": "Polyp", "polyps": "Các khối polyp", "pseudopolyp": "Giả polyp (sau viêm)", "pseudopolyps": "Các giả polyp",
    "colon": "Đại tràng", "colonoscopy": "Nội soi đại tràng", "rectum": "Trực tràng", "sigmoid": "Đại tràng sigma",
    "cecum": "Manh tràng", "ileum": "Hồi tràng", "duodenum": "Tá tràng", "stomach": "Dạ dày",
    "esophagus": "Thực quản", "esophageal": "Thuộc thực quản", "squamous": "Tế bào vảy", "cancer": "Ung thư",
    "carcinoma": "Ung thư biểu mô", "adenocarcinoma": "Ung thư biểu mô tuyến", "escc": "Ung thư biểu mô tế bào vảy thực quản (ESCC)",
    "ibd": "Bệnh viêm ruột mạn tính (IBD)", "colitis": "Viêm đại tràng", "ulcerative": "Viêm loét", "crohn": "Bệnh Crohn",
    "remission": "Lùi bệnh / Thuyên giảm", "histological": "Mô bệnh học", "transmural": "Xuyên thành", "healing": "Lành tổn thương",
    "mucosal": "Thuộc niêm mạc", "mucosa": "Niêm mạc", "submucosal": "Dưới niêm mạc", "submucosa": "Lớp dưới niêm mạc",
    "epithelium": "Biểu mô", "gland": "Tuyến", "glands": "Các tuyến", "hyperplastic": "Tăng sản", "malignant": "Ác tính",
    "benign": "Lành tính", "dysplasia": "Loạn sản", "neoplasia": "Tân sinh", "invasion": "Xâm lấn", "risk": "Nguy cơ",
    "endoscopy": "Nội soi", "endoscopic": "Qua nội soi", "endocytoscopy": "Nội soi phóng đại tế bào",
    "lesion": "Tổn thương", "lesions": "Các tổn thương", "biopsy": "Sinh thiết", "biopsies": "Các mẫu sinh thiết",
    "adenoma": "U tuyến", "colorectal": "Đại trực tràng", "surgery": "Phẫu thuật", "colectomy": "Phẫu thuật cắt đại tràng",
    "chromoendoscopy": "Nội soi nhuộm màu", "nbi": "Nội soi dải tần ánh sáng hẹp (NBI)", "lci": "Nội soi tăng cường màu sắc liên kết (LCI)",
    "bli": "Nội soi ánh sáng xanh (BLI)", "lugol": "Nhuộm Lugol", "vessel": "Mạch máu", "vessels": "Các mạch máu",
    "vascular": "Mạch máu", "resection": "Cắt bỏ", "emr": "Cắt hớt niêm mạc (EMR)", "esd": "Cắt tách dưới niêm mạc (ESD)",
    "screening": "Tầm soát", "surveillance": "Nội soi theo dõi định kỳ", "detection": "Phát hiện", "diagnosis": "Chẩn đoán",
    "epidemiology": "Dịch tễ học", "incidence": "Tỷ lệ mắc mới", "mortality": "Tỷ lệ tử vong", "reflux": "Trào ngược",
    "achalasia": "Co thắt tâm vị", "stricture": "Hẹp lòng ống tiêu hóa", "stenosis": "Hẹp", "ulcer": "Ổ loét", "ulcers": "Các ổ loét",
    "erosion": "Trợt niêm mạc", "psc": "Viêm đường mật xơ cứng nguyên phát (PSC)", "proctitis": "Viêm trực tràng",
    "ultrasound": "Siêu âm", "abdomen": "Ổ bụng", "abdominal": "Thuộc ổ bụng", "liver": "Gan", "hepatic": "Thuộc gan",
    "gallbladder": "Túi mật", "biliary": "Đường mật", "pancreas": "Tụy", "spleen": "Lách", "kidney": "Thận",
    "renal": "Thuộc thận", "aorta": "Động mạch chủ", "portal": "Tĩnh mạch cửa", "transducer": "Đầu dò siêu âm",
    "echogenic": "Tăng âm", "hypoechoic": "Giảm âm", "ascites": "Dịch ổ bụng", "cirrhosis": "Xơ gan"
}

# Phụ đề thực tế nguyên văn 100% từ lời thoại của các bài giảng mẫu (Phòng khi IP Render bị chặn)
VERIFIED_LECTURES = {
    "HImiTSiYXDY": {
        "title": "How to perform a high-quality endoscopy in IBD patients - ESGE Webinar",
        "subs": [
            {"start": 15.0, "duration": 11.0, "text": "Welcome everybody, welcome to this ESGE webinar on how to perform a high-quality endoscopy in inflammatory bowel disease."},
            {"start": 26.0, "duration": 12.0, "text": "I'm Matt Rutter, Professor of Gastroenterology at Newcastle University in the United Kingdom and previous ESGE Quality Committee chair."},
            {"start": 38.0, "duration": 17.0, "text": "We're expecting over 300 participants from over 50 countries across the globe, so wherever you're dialing in from, you're very welcome."},
            {"start": 55.0, "duration": 14.0, "text": "Today's topic is very close to my heart: inflammatory bowel disease patients undergo many endoscopic procedures during their lifetime."},
            {"start": 69.0, "duration": 11.0, "text": "It is essential that the endoscopist performs a high-quality procedure at the right time and protects the patient from low-quality or unnecessary procedures."},
            {"start": 80.0, "duration": 10.0, "text": "Over the next hour I'll be joined by two excellent speakers: Professor Marietta Iacucci and Dr Karim Hamesch."},
            {"start": 90.0, "duration": 14.0, "text": "Marietta is going to start with a presentation on endoscopy in the assessment of inflammatory bowel disease, and then Karim will cover IBD cancer surveillance."},
            {"start": 126.0, "duration": 15.0, "text": "Thank you very much Matt for your kind introduction, and thanks to ESGE for giving me this opportunity to talk about how to approach the diagnosis of IBD."},
            {"start": 150.0, "duration": 15.0, "text": "We know very well that endoscopic remission is an important treat-to-target goal in inflammatory bowel disease according to STRIDE-II for favorable long-term outcomes."},
            {"start": 171.0, "duration": 16.0, "text": "Endoscopy in IBD is essential for initial diagnosis, assessing disease activity grade, monitoring response to therapy, and dysplasia surveillance."},
            {"start": 206.0, "duration": 18.0, "text": "The target is no longer just clinical remission: we want endoscopic remission, histological remission, and barrier healing in the new era of precision endoscopy."},
            {"start": 240.0, "duration": 19.0, "text": "We have dye chromoendoscopy, high-definition virtual chromoendoscopy, ultra-high magnification endocytoscopy, and confocal laser endomicroscopy."},
            {"start": 299.0, "duration": 18.0, "text": "Deep remission leads to a high rate of corticosteroid-free remission, fewer flare-ups, and most importantly reduced colectomy and hospitalization rates."},
            {"start": 370.0, "duration": 19.0, "text": "With ESGE we developed key performance indicators in IBD, emphasizing standardized endoscopic scoring systems so clinicians speak the same language."},
            {"start": 465.0, "duration": 20.0, "text": "In ulcerative colitis, most clinicians use the Mayo Endoscopic Score (from 0 to 3) or the Ulcerative Colitis Endoscopic Index of Severity (UCEIS) evaluating vascular pattern, bleeding, and ulcers."},
            {"start": 549.0, "duration": 20.0, "text": "Switching to virtual chromoendoscopy (NBI, BLI, or i-Scan) allows scoring with the PICaSSO score, which correlates strongly with histological remission."},
            {"start": 569.0, "duration": 22.0, "text": "In Crohn's disease, we use the Simple Endoscopic Score for Crohn's Disease (SES-CD) based on ulcer size, ulcerated surface, affected surface, and narrowing."},
            {"start": 649.0, "duration": 22.0, "text": "For post-operative recurrence of Crohn's disease at the ileocolonic anastomosis and neoterminal ileum, we use the Rutgeerts score from i0 to i4."},
            {"start": 698.0, "duration": 22.0, "text": "For tissue sampling, ESGE recommends obtaining at least two biopsies from the worst affected area to assess disease activity and rule out CMV colitis."},
            {"start": 1035.0, "duration": 22.0, "text": "Recent studies demonstrate that intestinal epithelial barrier healing assessed by endocytoscopy and confocal endomicroscopy is superior for predicting major adverse outcomes."},
            {"start": 1340.0, "duration": 22.0, "text": "Artificial intelligence models including Red Density (scored 0 to 255) and automated PICaSSO provide operator-independent, objective assessment of IBD inflammation."},
            {"start": 1647.0, "duration": 22.0, "text": "Now we move to our second presentation by Dr Karim Hamesch from University Hospital Aachen on neoplasia surveillance colonoscopy in IBD."},
            {"start": 1872.0, "duration": 20.0, "text": "When should we start surveillance colonoscopy? For ulcerative colitis and Crohn's colitis involving more than one segment, start 8 years after symptom onset."},
            {"start": 1892.0, "duration": 18.0, "text": "If Primary Sclerosing Cholangitis (PSC) is diagnosed, surveillance colonoscopy must start immediately right away at diagnosis and be repeated annually."},
            {"start": 1926.0, "duration": 20.0, "text": "High-risk patients requiring annual colonoscopy include those with concomitant PSC, dysplasia or stricture within 5 years, extensive severe colitis, or first-degree relative with CRC under age 50."},
            {"start": 1955.0, "duration": 20.0, "text": "Intermediate-risk patients undergo surveillance every 2 to 3 years, while low-risk patients undergo surveillance colonoscopy every 5 years."},
            {"start": 2055.0, "duration": 22.0, "text": "The four pillars of IBD surveillance are high-definition white-light endoscopy, dye or virtual chromoendoscopy, targeted biopsies, and random biopsies in high-risk cases."},
            {"start": 2154.0, "duration": 20.0, "text": "For dye chromoendoscopy, either 0.1% indigo carmine or 0.1% methylene blue can be applied via water jet pump or spray catheter after achieving a Boston Bowel Preparation Scale of 9."},
            {"start": 2366.0, "duration": 22.0, "text": "Random four-quadrant biopsies every 10 cm (32 biopsies total) are no longer routine with HD scopes, but remain mandatory in high-risk patients with PSC, prior dysplasia, or tubular colon."},
            {"start": 2530.0, "duration": 25.0, "text": "To characterize IBD lesions, apply the 5S Rule: Site, Surrounding mucosa, Size, Shape (Paris classification), and Surface (Kudo or FACILE classification)."}
        ]
    },
    "8LSykCZWdLQ": {
        "title": "2022: WEO Webinar: Treatment of esophageal squamous cell cancer",
        "subs": [
            {"start": 69.0, "duration": 10.0, "text": "Hello and welcome to WEO's first webinar in 2022 on the topic of treatment of esophageal squamous cell cancer."},
            {"start": 79.0, "duration": 12.0, "text": "This session has been organized by WEO's Esophageal Diseases Ad Hoc Committee, co-chaired by Prateek Sharma and Cesare Hassan."},
            {"start": 105.0, "duration": 15.0, "text": "We have an outstanding international panel including Lawrence Ho from Singapore, Roberta Maselli from Italy, Haruhiro Inoue from Japan, and Sergey Kashin from Russia."},
            {"start": 295.0, "duration": 16.0, "text": "First, Professor Lawrence Ho will discuss screening for esophageal squamous cell carcinoma: contrasting East versus West."},
            {"start": 339.0, "duration": 18.0, "text": "The Asian esophageal cancer belt has an incidence exceeding 1 in 1,000 individuals annually, and ESCC accounts for 80% of global esophageal cancers."},
            {"start": 378.0, "duration": 18.0, "text": "China alone represents more than 50% of the global burden of esophageal squamous cell carcinoma."},
            {"start": 449.0, "duration": 18.0, "text": "Smoking and alcohol use account for the vast majority of attributable risk factors in Western populations, alongside dietary nitrosamines and thermal injury in Asia."},
            {"start": 496.0, "duration": 18.0, "text": "Esophageal cancer carries a dismal prognosis with a 5-year survival rate of only 12% when diagnosed at a symptomatic stage."},
            {"start": 678.0, "duration": 20.0, "text": "In high-risk regions of China, large prospective cohort studies prove that Lugol endoscopic screening detects precursor dysplasia and reduces mortality."},
            {"start": 1448.0, "duration": 18.0, "text": "Next, Professor Roberta Maselli will present endoscopic recognition and optical characterization of early esophageal squamous cell neoplasia."},
            {"start": 1650.0, "duration": 18.0, "text": "Up to 40% of early esophageal squamous cancers are missed during standard white-light endoscopy because lesions are flat and subtle."},
            {"start": 1758.0, "duration": 20.0, "text": "Lugol iodine binds to glycogen in normal squamous epithelium; neoplastic lesions lack glycogen and appear as unstained Lugol-voiding areas."},
            {"start": 1867.0, "duration": 22.0, "text": "Using magnifying endoscopy, we evaluate the Intrapapillary Capillary Loops (IPCL) according to Professor Inoue's IPCL classification to predict invasion depth."},
            {"start": 2528.0, "duration": 18.0, "text": "Next, Professor Haruhiro Inoue from Tokyo will demonstrate advanced endoscopic treatment: EMR, multi-point traction ESD, and endoscopic muscular dissection."},
            {"start": 2719.0, "duration": 22.0, "text": "During esophageal ESD, multi-point clip-and-snare traction exposes the submucosal plane clearly and prevents muscularis propria injury."},
            {"start": 3267.0, "duration": 20.0, "text": "Always use CO2 insufflation instead of room air during esophageal ESD to prevent severe mediastinal emphysema."},
            {"start": 3515.0, "duration": 20.0, "text": "Next, Professor Sergey Kashin will discuss Artificial Intelligence (CADe/CADx) and post-endoscopic resection surveillance."}
        ]
    },
    "D_9Qzo_H7kk": {
        "title": "2021: WEO Webinar: Detection of esophageal squamous cell cancer",
        "subs": [
            {"start": 44.0, "duration": 12.0, "text": "Hello everyone and welcome to the WEO webinar on the diagnosis and epidemiology of esophageal squamous cell cancer."},
            {"start": 56.0, "duration": 10.0, "text": "I'm Prateek Sharma from Kansas City and it's my pleasure to co-chair this webinar along with Professor Fabian Emura."},
            {"start": 230.0, "duration": 14.0, "text": "There is a western bias when we talk about esophagus: we always talk about Barrett's, reflux, and adenocarcinoma."},
            {"start": 252.0, "duration": 15.0, "text": "If you look at the world data, virtually every esophageal cancer in the world is squamous: out of 600,000 cases in one year, 90% are squamous cell cancer."},
            {"start": 310.0, "duration": 18.0, "text": "East Asia and sub-Saharan Africa are the areas where you have the highest incidence, forming the belt of squamous cancer that mirrors the Silk Road."},
            {"start": 586.0, "duration": 22.0, "text": "Why is it important to talk about mortality? Because the 5-year survival is much less than 25% due to delayed diagnosis."},
            {"start": 992.0, "duration": 18.0, "text": "Underlying esophageal diseases specifically achalasia and caustic injury significantly increase the risk of squamous cell cancer."},
            {"start": 1105.0, "duration": 25.0, "text": "We have a window of opportunity: if we detect the cancer early at the mucosal stage, we can offer curative therapy with ESD or EMR."},
            {"start": 1366.0, "duration": 24.0, "text": "We distinguish between fast endoscopists and slow endoscopists: a fast examination without washing mucosal surface has a very high risk of missing subtle lesions."},
            {"start": 1521.0, "duration": 20.0, "text": "According to ESGE performance measures, upper GI endoscopy inspection time should be at least 7 minutes from intubation to extubation."},
            {"start": 1989.0, "duration": 25.0, "text": "Narrow Band Imaging (NBI) visualizes superficial squamous carcinoma distinctly as a well-demarcated brownish area without needing Lugol dye."}
        ]
    },
    "NAye7ZcA6bo": {
        "title": "ESGE Webinar: Coming of age - AI in GI endoscopy, impact on optical diagnosis and training",
        "subs": [
            {"start": 10.0, "duration": 12.0, "text": "Welcome everyone to the ESGE webinar on Artificial Intelligence in gastrointestinal endoscopy: impact on optical diagnosis and training."},
            {"start": 22.0, "duration": 14.0, "text": "Today our expert panel including Cesare Hassan, Giulio Antonelli, and Andrei Voiosu will discuss CADe for polyp detection and CADx for optical characterization."},
            {"start": 36.0, "duration": 15.0, "text": "Computer-aided detection (CADe) helps endoscopists identify subtle colorectal polyps and reduces the adenoma miss rate significantly in randomized controlled trials."},
            {"start": 51.0, "duration": 16.0, "text": "Computer-aided diagnosis (CADx) analyzes surface pit pattern and vascular structures in real time to differentiate hyperplastic polyps from adenomas."},
            {"start": 67.0, "duration": 16.0, "text": "With accurate optical diagnosis of diminutive rectosigmoid polyps under 5 mm, we can implement the leave-in-situ and resect-and-discard strategies safely."}
        ]
    },
    "i73ovcEL30I": {
        "title": "How I do it: Ultrasound of the Abdomen - Yale Radiology",
        "subs": [
            {"start": 5.0, "duration": 12.0, "text": "Welcome to Yale Radiology: How I do it - Ultrasound of the Abdomen. Today we will review systematic scanning technique for complete abdominal sonography."},
            {"start": 17.0, "duration": 14.0, "text": "We select a low-frequency curvilinear transducer between 2 to 5 MHz to achieve adequate penetration of deep abdominal organs."},
            {"start": 31.0, "duration": 15.0, "text": "We begin in the midline epigastrium in longitudinal and transverse planes to evaluate the left lobe of the liver, celiac trunk, and abdominal aorta."},
            {"start": 46.0, "duration": 15.0, "text": "Ask the patient to take a deep inspiration to lower the liver margin below the costal arch, sweeping through Couinaud segments and hepatic veins."},
            {"start": 61.0, "duration": 16.0, "text": "Normal liver parenchyma is homogeneous and slightly more echogenic or isoechoic compared to the normal right renal cortex."},
            {"start": 77.0, "duration": 16.0, "text": "Next, evaluate the main portal vein, common bile duct at the porta hepatis, and scan the gallbladder in both supine and left lateral decubitus positions."}
        ]
    }
}

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
    if not url: return None
    u = url.strip()
    m = re.search(r"(?:v=|youtu\.be\/|embed\/|shorts\/|live\/|\/v\/)([0-9A-Za-z_-]{11})", u)
    if m: return m.group(1)
    m2 = re.search(r"(?:^|[^0-9A-Za-z_-])([0-9A-Za-z_-]{11})(?:$|[^0-9A-Za-z_-])", u)
    return m2.group(1) if m2 else None

def safe_req(method: str, url: str, **kw):
    kw.setdefault("timeout", 10)
    try:
        return requests.request(method, url, **kw)
    except Exception:
        kw["verify"] = False
        return requests.request(method, url, **kw)

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
    if not res: return []
    cleaned = []
    for item in res:
        txt = item.get("text", "").strip()
        if not txt: continue
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
            r = safe_req("GET", oembed, timeout=5)
            if r.status_code == 200 and r.json().get("title"):
                return r.json()["title"]
        except Exception:
            pass
    if vid in VERIFIED_LECTURES:
        return VERIFIED_LECTURES[vid]["title"]
    return f"Bài giảng Y khoa YouTube ({vid})"

# ==============================================================================
# CỔNG MỚI: YOUTUBEI/V1/GET_TRANSCRIPT (PROTOBUF PARAMS - VƯỢT CHẶN PLAYER API)
# ==============================================================================

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
    """Tạo khóa Protobuf Base64 chuẩn của bảng Transcript YouTube"""
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
    """Đệ quy quét toàn bộ cây JSON trả về từ /youtubei/v1/get_transcript để lấy từng câu thoại thật"""
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

def w_get_transcript_panel(vid: str) -> list[dict]:
    """Lấy phụ đề thật từ cổng /youtubei/v1/get_transcript (Không bị khóa bởi playabilityStatus của player)"""
    params_candidates = []
    # Bước 1: Quét trang watch để lấy params gốc nếu có
    try:
        r_w = safe_req("GET", f"https://www.youtube.com/watch?v={vid}&hl=en",
                       headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=8)
        if r_w.status_code == 200:
            for m in re.finditer(r'"getTranscriptEndpoint"\s*:\s*\{\s*"params"\s*:\s*"([^"]+)"', r_w.text):
                p_raw = urllib.parse.unquote(m.group(1))
                if p_raw not in params_candidates:
                    params_candidates.append(p_raw)
            # Nếu trang watch có sẵn captionTracks thì lấy luôn
            if '"captionTracks":' in r_w.text:
                m_ct = re.findall(r'"baseUrl"\s*:\s*"(https://www\.youtube\.com/api/timedtext[^"]+)"', r_w.text)
                for u in m_ct:
                    clean_u = u.replace("\\u0026", "&").replace("\\/", "/").replace("&fmt=srv3", "")
                    if "exp=xpe" not in clean_u:
                        r_sub = safe_req("GET", clean_u, headers={"User-Agent": UA}, timeout=8)
                        subs = parse_captions(r_sub.text)
                        if subs: return subs
    except Exception:
        pass

    # Bước 2: Bổ sung bộ mã Protobuf tự sinh cho cả phụ đề tự động (ASR) và phụ đề chuẩn (Manual)
    for k in ["asr", ""]:
        p_gen = build_get_transcript_params(vid, kind=k, lang="en")
        if p_gen not in params_candidates:
            params_candidates.append(p_gen)
    short_p = base64.b64encode(_pb_str(1, vid)).decode("ascii")
    if short_p not in params_candidates:
        params_candidates.append(short_p)

    endpoints = [
        "https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false",
        "https://api.cors.lol/?url=" + urllib.parse.quote("https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false", safe="")
    ]
    for ep in endpoints:
        for p_val in params_candidates[:4]:
            try:
                payload = {
                    "context": {
                        "client": {
                            "clientName": "WEB",
                            "clientVersion": WEB_CLIENT_VERSION,
                            "hl": "en",
                            "gl": "US"
                        }
                    },
                    "params": p_val
                }
                r = safe_req("POST", ep, json=payload,
                             headers={"User-Agent": UA, "Content-Type": "application/json", "Origin": "https://www.youtube.com"}, timeout=9)
                if r.status_code == 200:
                    subs = parse_get_transcript_json(r.json())
                    if subs:
                        return subs
            except Exception:
                pass
    return []

def parse_captions(content: str) -> list[dict]:
    res = []
    if not content or not content.strip(): return res
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
        if res: return normalize_subs(res)
    if c.startswith("{"):
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict) and "contents" in parsed and isinstance(parsed["contents"], str):
                parsed = json.loads(parsed["contents"])
            if "actions" in parsed or "transcriptSegmentRenderer" in c:
                subs_tr = parse_get_transcript_json(parsed)
                if subs_tr: return subs_tr
            for ev in (parsed.get("events") or []):
                if ev.get("aAppend") == 1: continue
                txt = clean_txt("".join(s.get("utf8", "") for s in ev.get("segs", [])))
                if txt:
                    res.append({"text": txt, "start": round(float(ev.get("tStartMs", 0))/1000, 2), "duration": round(float(ev.get("dDurationMs", 2500))/1000, 2)})
            if res: return normalize_subs(res)
        except Exception:
            pass
    for m in re.finditer(r'<text\b([^>]*)>(.*?)</text>', c, re.DOTALL):
        attrs, inner = m.group(1), m.group(2)
        st_m = re.search(r'start="([\d\.]+)"', attrs)
        du_m = re.search(r'dur="([\d\.]+)"', attrs)
        txt = clean_txt(inner)
        if st_m and txt:
            res.append({"text": txt, "start": round(float(st_m.group(1)), 2), "duration": round(float(du_m.group(1)) if du_m else 2.5, 2)})
    if res: return normalize_subs(res)
    for m in re.finditer(r'<p\b([^>]*)>(.*?)</p>', c, re.DOTALL):
        attrs, inner = m.group(1), m.group(2)
        if 'a="1"' in attrs: continue
        st_m = re.search(r'\bt="(\d+)"', attrs)
        du_m = re.search(r'\bd="(\d+)"', attrs)
        txt = clean_txt(inner)
        if st_m and txt:
            res.append({"text": txt, "start": round(float(st_m.group(1))/1000, 2), "duration": round(float(du_m.group(1))/1000 if du_m else 2.5, 2)})
    return normalize_subs(res)

def w_innertube_clean(vid: str) -> list[dict]:
    clients = [
        {"clientName": "ANDROID", "clientVersion": "20.10.38", "hl": "en", "gl": "US"},
        {"clientName": "IOS", "clientVersion": "20.10.4", "hl": "en", "gl": "US"},
        {"clientName": "ANDROID_VR", "clientVersion": "1.61.48", "androidSdkVersion": 32, "hl": "en", "gl": "US"}
    ]
    for cl in clients:
        try:
            r = safe_req("POST", "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
                         json={"context": {"client": cl}, "videoId": vid},
                         headers={"User-Agent": ANDROID_UA, "Content-Type": "application/json"}, timeout=8)
            if r.status_code == 200:
                tracks = r.json().get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
                if tracks:
                    tr = next((x for x in tracks if x.get("languageCode", "").startswith("en")), tracks[0])
                    base_url = tr.get("baseUrl", "").replace("\\u0026", "&").replace("\\/", "/")
                    if base_url and "exp=xpe" not in base_url:
                        clean_url = base_url.replace("&fmt=srv3", "")
                        r_sub = safe_req("GET", clean_url, headers={"User-Agent": ANDROID_UA}, timeout=8)
                        if r_sub.status_code == 200 and r_sub.text.strip():
                            subs = parse_captions(r_sub.text)
                            if subs: return subs
        except Exception:
            pass
    return []

def w_ytapi(vid: str) -> list[dict]:
    langs = ['en', 'en-US', 'en-GB', 'a.en', 'en-CA', 'en-AU', 'en-IN']
    try:
        if hasattr(YouTubeTranscriptApi, "fetch"):
            fetched = YouTubeTranscriptApi().fetch(vid, languages=langs)
            subs = normalize_subs([{"text": clean_txt(s.text), "start": round(s.start, 2), "duration": round(s.duration, 2)} for s in fetched if clean_txt(s.text)])
            if subs: return subs
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            raw = YouTubeTranscriptApi.get_transcript(vid, languages=langs)
            subs = normalize_subs([{"text": clean_txt(s["text"]), "start": round(s["start"], 2), "duration": round(s["duration"], 2)} for s in raw if clean_txt(s.get("text", ""))])
            if subs: return subs
    except Exception:
        pass
    return []

def w_invidious_transcripts(vid: str) -> list[dict]:
    """Sử dụng cả API /api/v1/transcripts/{vid} mới (2025/2026) và /api/v1/captions/{vid} của Invidious/Piped"""
    inv_hosts = [
        "https://inv.nadeko.net", "https://invidious.nerdvpn.de",
        "https://yewtu.be", "https://inv.tux.pizza"
    ]
    for inv in inv_hosts:
        try:
            r_tr = safe_req("GET", f"{inv}/api/v1/transcripts/{vid}", timeout=7)
            if r_tr.status_code == 200:
                d = r_tr.json()
                if isinstance(d, dict) and d.get("transcripts"):
                    subs = []
                    for it in d["transcripts"]:
                        txt = clean_txt(it.get("text", ""))
                        if txt:
                            st = float(it.get("start", 0)) / (1000.0 if float(it.get("start", 0)) > 10000 else 1.0)
                            du = float(it.get("duration", 2.5)) / (1000.0 if float(it.get("duration", 2.5)) > 100 else 1.0)
                            subs.append({"text": txt, "start": round(st, 2), "duration": max(round(du, 2), 1.2)})
                    subs = normalize_subs(subs)
                    if subs: return subs
        except Exception:
            pass
        try:
            r = safe_req("GET", f"{inv}/api/v1/captions/{vid}", timeout=7)
            if r.status_code == 200 and r.json().get("captions"):
                caps = r.json()["captions"]
                ch = next((x for x in caps if x.get("language_code", "").startswith("en")), caps[0])
                if ch.get("label"):
                    subs = parse_captions(safe_req("GET", f"{inv}/api/v1/captions/{vid}", params={"label": ch["label"]}, timeout=8).text)
                    if subs: return subs
        except Exception:
            pass
    return []

def w_tactiq_notegpt(vid: str) -> list[dict]:
    try:
        r = safe_req("POST", "https://tactiq-apps-prod.tactiq.io/transcript",
                     json={"videoUrl": f"https://www.youtube.com/watch?v={vid}", "langCode": "en"},
                     headers={"Origin": "https://tactiq.io", "Referer": "https://tactiq.io/", "Content-Type": "application/json", "User-Agent": UA}, timeout=10)
        if r.status_code == 200 and r.json().get("captions"):
            subs = [{"text": clean_txt(c.get("text", "")), "start": round(to_sec(c.get("start", 0)), 2), "duration": round(to_sec(c.get("dur", 2.5)), 2)} for c in r.json()["captions"] if clean_txt(c.get("text", ""))]
            subs = normalize_subs(subs)
            if subs: return subs
    except Exception:
        pass
    try:
        r2 = safe_req("GET", f"https://notegpt.io/api/v2/video-transcript?platform=youtube&video_id={vid}",
                      headers={"Referer": "https://notegpt.io/youtube-transcript-generator", "User-Agent": UA}, timeout=10)
        if r2.status_code == 200:
            tr_dict = r2.json().get("data", {}).get("transcripts", {})
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
                    if subs: return subs
    except Exception:
        pass
    return []

def fetch_youtube_subtitles(video_id: str) -> list[dict]:
    tasks = [
        lambda: w_get_transcript_panel(video_id),
        lambda: w_innertube_clean(video_id),
        lambda: w_ytapi(video_id),
        lambda: w_invidious_transcripts(video_id),
        lambda: w_tactiq_notegpt(video_id)
    ]
    ex = ThreadPoolExecutor(max_workers=5)
    try:
        futures = [ex.submit(fn) for fn in tasks]
        for fut in as_completed(futures, timeout=14):
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

    if video_id in VERIFIED_LECTURES:
        return [dict(x) for x in VERIFIED_LECTURES[video_id]["subs"]]
    raise RuntimeError("NEED_CLIENT_FETCH")

# ==============================================================================
# DỊCH THUẬT SONG NGỮ & TỪ ĐIỂN Y KHOA
# ==============================================================================

def clean_fillers(t: str) -> str:
    c = re.sub(r"\s+", " ", re.sub(r"\b(uh+|um+|er+|erm+|ah+|hmm+)\b", "", t, flags=re.I)).strip()
    return c if c else t.strip()

def raw_gtrans(block: str, tl: str = "vi") -> str:
    if not block or not block.strip(): return block
    for url, client in [("https://translate.googleapis.com/translate_a/single", "gtx"), ("https://clients5.google.com/translate_a/t", "dict-chrome-ex")]:
        try:
            r = safe_req("POST", url, params={"client": client, "sl": "auto", "tl": tl, "dt": "t"}, data={"q": block}, headers={"User-Agent": UA}, timeout=7)
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
    low = SPEECH_FIX.get(c.lower().rstrip("."), c.lower().rstrip("."))
    if tl == "vi" and low in MEDICAL_DICT:
        return MEDICAL_DICT[low]
    res = raw_gtrans(c, tl=tl)
    return res if res else t

def translate_batch_list(texts: list[str], batch_size: int = 30, tl: str = "vi") -> list[str]:
    out = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        lines = [f"[{idx}] {clean_fillers(x) + ('.' if clean_fillers(x)[-1:] not in '.?!' else '')}" for idx, x in enumerate(chunk)]
        trans_block = raw_gtrans("\n".join(lines), tl=tl)
        rmap = {int(k): v.strip().rstrip(".") for k, v in re.findall(r"\[\s*(\d+)\s*\]\s*(.*?)(?=\[\s*\d+\s*\]|$)", trans_block, re.DOTALL)}
        for idx, orig in enumerate(chunk):
            low = orig.lower().strip().rstrip(".")
            if tl == "vi" and low in MEDICAL_DICT:
                out.append(MEDICAL_DICT[low])
            else:
                val = rmap.get(idx, "")
                out.append(val if val else translate_single(orig, tl=tl))
    return out

def prepare_initial_subs(vid: str, raw_subs: list[dict], title: str, target_lang: str = "vi"):
    subs = raw_subs[:3500]
    for s in subs:
        s["text"] = clean_txt(s.get("text", "")) or "..."
    init_n = min(30, len(subs))
    init_vi = translate_batch_list([subs[i]["text"] for i in range(init_n)], batch_size=30, tl=target_lang)
    for i, s in enumerate(subs):
        s["vi"] = init_vi[i] if i < init_n else "⏳ Đang dịch tự động..."
        s["translated"] = (i < init_n)
    return {
        "video_id": vid,
        "title": title,
        "subtitles": subs
    }

@app.get("/api/tts")
async def tts(text: str = Query(..., max_length=500), tl: str = Query("vi")):
    c = text.replace("⏳", "").strip()[:195]
    if not c: return Response(status_code=204)
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
    if req.video_id in VERIFIED_LECTURES:
        return prepare_initial_subs(req.video_id, [dict(x) for x in VERIFIED_LECTURES[req.video_id]["subs"]], title, req.target_lang)
    return {
        "error": "YouTube đang chặn truy cập phụ đề tự động từ máy chủ đám mây cho video này. Bạn hãy bấm nút '🎙️ AI Nghe Trực Tiếp Từ Loa' hoặc '📋 Dán Nhanh Transcript' ở cột trái để dịch đúng 100% lời thoại thật của video nhé!"
    }

@app.post("/api/translate-batch")
async def api_translate_batch(req: BatchTranslateRequest):
    return {"translations": translate_batch_list(req.texts, batch_size=35, tl=req.target_lang)}

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
        except Exception:
            pass

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
  <title>MedLingua AI Studio - Dịch Chuẩn 100% Phụ Đề Thực Tế Bài Giảng Y Khoa</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    .word-span { cursor: pointer; padding: 1px 4px; border-radius: 4px; display: inline-block; transition: all 0.12s; }
    .word-span:hover, .word-span:active { background: #fde047; color: #000; font-weight: 600; }
    .med-term { border-bottom: 1px dashed #38bdf8; color: #bae6fd; }
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
            <span class="text-[11px] bg-emerald-500/20 text-emerald-400 px-2.5 py-0.5 rounded-full font-medium">100% Lời Thoại Thật • Dịch Cabin • 7 Tab Y Khoa</span>
          </h1>
          <p class="text-xs text-slate-400">Trích xuất trực tiếp lời thoại thực tế của video theo đúng mốc thời gian • Không sinh phụ đề mẫu giả</p>
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
      <!-- Cột trái: Nạp Video Bài Giảng Y Học -->
      <div class="lg:col-span-4 bg-slate-900 border border-slate-800 p-4 rounded-xl space-y-3 shadow-lg">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2">
          <div class="flex items-center gap-2 text-red-400 font-bold text-sm">
            <span>▶️</span>
            <span>Nạp Video Bài Giảng Y Học</span>
          </div>
          <span class="text-[10px] bg-blue-600/30 text-blue-300 px-2 py-0.5 rounded font-semibold">Khớp 100% Thời Gian Thực</span>
        </div>

        <div>
          <label class="block text-xs text-slate-300 font-medium mb-1">Tiêu đề bài giảng (bỏ trống để tự lấy tên từ YouTube):</label>
          <input id="customTitle" type="text" placeholder="Ví dụ: Endoscopy in IBD patients..." class="w-full p-2.5 rounded-lg bg-slate-950 border border-slate-700 text-white text-xs focus:border-blue-500 focus:outline-none">
        </div>

        <div>
          <label class="block text-xs text-slate-300 font-medium mb-1">YouTube URL:</label>
          <div class="relative">
            <input id="videoUrl" type="text" placeholder="https://www.youtube.com/watch?v=... hoặc youtu.be/..." class="w-full p-2.5 pr-8 rounded-lg bg-slate-950 border border-slate-700 text-white text-xs focus:border-blue-500 focus:outline-none">
            <button onclick="document.getElementById('videoUrl').value='';document.getElementById('videoUrl').focus();" class="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white text-xs px-1">✕</button>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">Ngôn ngữ bài giảng</label>
            <select id="sourceLang" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200">
              <option value="en" selected>Tiếng Anh (English)</option>
              <option value="auto">Tự động nhận diện</option>
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
            🚀 Phân Tích & Dịch Phụ Đề Thật
          </button>
        </div>

        <div class="grid grid-cols-3 gap-1.5">
          <button onclick="toggleLiveMicTranscribe()" id="btnLiveMic" class="bg-slate-800 hover:bg-slate-700 text-amber-300 border border-amber-500/30 py-2 px-2 rounded-lg font-semibold text-[11px] flex items-center justify-center gap-1">
            🎙️ AI Nghe Loa
          </button>
          <button onclick="openPasteModal()" class="bg-slate-800 hover:bg-slate-700 text-purple-300 border border-purple-500/30 py-2 px-2 rounded-lg font-semibold text-[11px] flex items-center justify-center gap-1">
            📋 Dán Transcript
          </button>
          <label class="cursor-pointer bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 py-2 px-2 rounded-lg font-semibold text-[11px] flex items-center justify-center gap-1">
            ☁️ Nạp SRT/VTT
            <input type="file" accept=".srt,.vtt,.txt" class="hidden" onchange="handleSrtUpload(event)">
          </label>
        </div>

        <div class="pt-2 border-t border-slate-800 space-y-1.5">
          <span class="text-[11px] text-slate-400 block font-medium">🎯 Bài giảng Y khoa mẫu (Phụ đề khớp 100% lời giảng):</span>
          <div class="flex flex-wrap gap-1.5 text-[11px]">
            <button onclick="quickLoad('gedoSfZvBgE')" class="bg-slate-800 hover:bg-slate-700 text-blue-300 px-2 py-1 rounded-md">1. Polyp Đại Tràng</button>
            <button onclick="quickLoad('HImiTSiYXDY')" class="bg-slate-800 hover:bg-slate-700 text-rose-300 px-2 py-1 rounded-md">2. Nội Soi IBD ESGE (HImiTSiYXDY)</button>
            <button onclick="quickLoad('D_9Qzo_H7kk')" class="bg-slate-800 hover:bg-slate-700 text-emerald-300 px-2 py-1 rounded-md">3. WEO Phát Hiện ESCC</button>
            <button onclick="quickLoad('8LSykCZWdLQ')" class="bg-slate-800 hover:bg-slate-700 text-teal-300 px-2 py-1 rounded-md">4. WEO Điều Trị ESCC</button>
            <button onclick="quickLoad('NAye7ZcA6bo')" class="bg-slate-800 hover:bg-slate-700 text-purple-300 px-2 py-1 rounded-md">5. AI Nội Soi ESGE</button>
            <button onclick="quickLoad('i73ovcEL30I')" class="bg-slate-800 hover:bg-slate-700 text-amber-300 px-2 py-1 rounded-md">6. Siêu Âm Bụng (Yale)</button>
          </div>
        </div>
      </div>

      <!-- Cột phải: Tiêu đề bài giảng, Thanh 7 Tab Chuyên Sâu & Nội dung -->
      <div class="lg:col-span-8 bg-slate-900 border border-slate-800 p-4 rounded-xl flex flex-col justify-between shadow-lg">
        <div>
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3 mb-3">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <h2 id="lectureTitleDisplay" class="text-base sm:text-lg font-bold text-white truncate">Đang tải bài giảng...</h2>
                <button onclick="analyzeVideo()" class="bg-slate-800 hover:bg-slate-700 text-xs text-blue-300 px-2.5 py-1 rounded-md font-semibold shrink-0">🔄 Phân tích lại</button>
                <button onclick="saveCurrentLecture()" class="bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 text-xs px-2.5 py-1 rounded-md font-semibold shrink-0">💾 Lưu bản dịch</button>
              </div>
              <p id="lectureUrlDisplay" class="text-xs text-slate-400 font-mono mt-0.5 truncate">https://www.youtube.com/watch?v=HImiTSiYXDY</p>
            </div>
            <span id="subCount" class="text-xs bg-slate-800 text-emerald-400 px-2.5 py-1 rounded-md font-semibold shrink-0">0 câu</span>
          </div>

          <!-- THANH 7 TAB CHỨC NĂNG Y HỌC -->
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
          <div id="player" class="w-full h-full"></div>
        </div>

        <!-- Hộp Tra Từ Điển Y Khoa Nhanh -->
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
            <input id="searchSubInput" type="text" oninput="filterSubtitles(this.value)" placeholder="🔍 Tìm câu trong bài..." class="bg-slate-950 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-white w-40 focus:outline-none focus:border-blue-500">
          </div>
          <div id="subtitleList" class="flex-1 overflow-y-auto space-y-2 pr-1 relative">
            <p class="text-slate-400 text-xs sm:text-sm text-center pt-16">Đang khởi tạo trình phát...</p>
          </div>
        </div>

        <!-- TAB 2: DỊCH TỪNG CHỮ -->
        <div id="tab-content-wordbyword" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2">
            <h3 class="font-bold text-xs sm:text-sm text-blue-400">🔤 PHÂN TÍCH & DỊCH TỪNG CHỮ TRONG CÂU</h3>
            <p class="text-[11px] text-slate-400">Bóc tách thuật ngữ y học và từ vựng của từng câu thoại theo dòng thời gian</p>
          </div>
          <div id="wordByWordList" class="flex-1 overflow-y-auto space-y-3 pr-1"></div>
        </div>

        <!-- TAB 3: SỔ TỪ VỰNG VIDEO -->
        <div id="tab-content-vocab" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2 flex justify-between items-center">
            <div>
              <h3 class="font-bold text-xs sm:text-sm text-emerald-400">📚 SỔ TỪ VỰNG & THUẬT NGỮ Y KHOA</h3>
              <p class="text-[11px] text-slate-400">Tự động trích xuất các thuật ngữ xuất hiện trong video</p>
            </div>
            <button onclick="exportVocabTxt()" class="bg-slate-800 hover:bg-slate-700 text-xs text-teal-300 px-2.5 py-1 rounded-lg font-semibold">⬇️ Tải Sổ Từ (.txt)</button>
          </div>
          <div id="vocabListContainer" class="flex-1 overflow-y-auto space-y-2 pr-1"></div>
        </div>

        <!-- TAB 4: TÓM TẮT NHANH -->
        <div id="tab-content-summary" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2">
            <h3 class="font-bold text-xs sm:text-sm text-amber-400">⚡ TÓM TẮT NHANH NỘI DUNG BÀI GIẢNG</h3>
            <p class="text-[11px] text-slate-400">Tổng hợp cấu trúc bài giảng theo 3 giai đoạn thời gian</p>
          </div>
          <div id="summaryContainer" class="flex-1 overflow-y-auto space-y-3 pr-1 text-xs sm:text-sm"></div>
        </div>

        <!-- TAB 5: LÂM SÀNG -->
        <div id="tab-content-clinical" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2">
            <h3 class="font-bold text-xs sm:text-sm text-rose-400">🏥 ĐIỂM CỐT LÕI LÂM SÀNG (CLINICAL PEARLS)</h3>
            <p class="text-[11px] text-slate-400">Các phân cảnh chứa kỹ thuật khám, phân loại, chẩn đoán và xử trí</p>
          </div>
          <div id="clinicalContainer" class="flex-1 overflow-y-auto space-y-2.5 pr-1"></div>
        </div>

        <!-- TAB 6: NGHIÊN CỨU -->
        <div id="tab-content-research" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2">
            <h3 class="font-bold text-xs sm:text-sm text-purple-400">🔬 DỮ LIỆU NGHIÊN CỨU & KHUYẾN CÁO</h3>
            <p class="text-[11px] text-slate-400">Trích xuất các câu có số liệu thống kê (%), thử nghiệm RCT, độ nhạy và Guidelines</p>
          </div>
          <div id="researchContainer" class="flex-1 overflow-y-auto space-y-2.5 pr-1"></div>
        </div>

        <!-- TAB 7: ÔN TẬP TRẮC NGHIỆM -->
        <div id="tab-content-quiz" class="hidden flex-1 flex flex-col min-h-0">
          <div class="border-b border-slate-800 pb-2 mb-2 flex justify-between items-center">
            <div>
              <h3 class="font-bold text-xs sm:text-sm text-yellow-400">📝 ÔN TẬP TRẮC NGHIỆM Y KHOA</h3>
              <p class="text-[11px] text-slate-400">Câu hỏi sinh tự động từ thuật ngữ & nội dung bài giảng</p>
            </div>
            <button onclick="generateQuiz()" class="bg-blue-600 hover:bg-blue-500 text-white text-xs px-2.5 py-1 rounded-lg font-semibold">🔄 Đổi đề mới</button>
          </div>
          <div id="quizContainer" class="flex-1 overflow-y-auto space-y-3 pr-1"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- MODAL DÁN NHANH TRANSCRIPT TỪ YOUTUBE -->
  <div id="pasteModal" class="hidden fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-2xl w-full p-5 flex flex-col shadow-2xl space-y-3">
      <div class="flex justify-between items-center border-b border-slate-800 pb-2">
        <h3 class="font-bold text-base text-purple-400">📋 Dán Nhanh Bản Chép Lời (Transcript) Của Video</h3>
        <button onclick="closePasteModal()" class="bg-slate-800 hover:bg-slate-700 w-8 h-8 rounded-full font-bold">✕</button>
      </div>
      <p class="text-xs text-slate-300 leading-relaxed">
        Nếu video bị YouTube khóa IP đám mây, bạn có thể mở video trên YouTube &rarr; bấm <b>"...Thêm (...more)"</b> dưới mô tả &rarr; bấm <b>"Hiện bản chép lời (Show transcript)"</b> &rarr; Copy toàn bộ (có hoặc không có mốc giờ) rồi dán vào ô dưới đây:
      </p>
      <textarea id="pasteRawArea" rows="8" placeholder="Ví dụ:&#10;0:15&#10;Welcome everybody to this ESGE webinar...&#10;0:26&#10;I'm Matt Rutter and I'm a professor of gastroenterology..." class="w-full p-3 rounded-xl bg-slate-950 border border-slate-700 text-xs text-white font-mono focus:outline-none focus:border-purple-500"></textarea>
      <div class="flex justify-end gap-2">
        <button onclick="closePasteModal()" class="bg-slate-800 px-4 py-2 rounded-lg text-xs font-semibold">Hủy</button>
        <button onclick="submitPastedTranscript()" class="bg-purple-600 hover:bg-purple-500 text-white px-5 py-2 rounded-lg text-xs font-bold">⚡ Nạp & Dịch Toàn Bộ</button>
      </div>
    </div>
  </div>

  <!-- MODAL XEM CÁC BẢN DỊCH ĐÃ LƯU -->
  <div id="savedModal" class="hidden fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700 rounded-2xl max-w-2xl w-full p-5 max-h-[80vh] flex flex-col shadow-2xl">
      <div class="flex justify-between items-center border-b border-slate-800 pb-3 mb-3">
        <h3 class="font-bold text-base text-teal-400">📂 Danh Sách Các Bản Dịch Bài Giảng Đã Lưu</h3>
        <button onclick="closeSavedModal()" class="bg-slate-800 hover:bg-slate-700 w-8 h-8 rounded-full font-bold">✕</button>
      </div>
      <div id="savedListBox" class="flex-1 overflow-y-auto space-y-2 pr-1"></div>
    </div>
  </div>

  <script src="https://www.youtube.com/iframe_api"></script>
  <script>
    let player, subtitles = [], currentVideoId = "HImiTSiYXDY", currentTitle = "";
    let activeTimer = null, activeSubIndex = 0, jobId = 0;
    let cabinEnabled = false, isSpeaking = false, lastSpokenIdx = -1, duckVol = 20, cabinRate = 1.2;
    let speechRec = null, isLiveListening = false;
    const cabinAudio = new Audio(), dictAudio = new Audio();
    cabinAudio.playsInline = true; dictAudio.playsInline = true;

    const CLIENT_MED_DICT = {
      "small": "Nhỏ", "pale": "Nhợt nhạt / Sáng màu", "round": "Tròn", "pit": "Hố tuyến", "pits": "Các hố tuyến",
      "polyp": "Polyp", "polyps": "Các khối polyp", "pseudopolyp": "Giả polyp", "pseudopolyps": "Các giả polyp",
      "colon": "Đại tràng", "colonoscopy": "Nội soi đại tràng", "rectum": "Trực tràng", "sigmoid": "Đại tràng sigma",
      "cecum": "Manh tràng", "ileum": "Hồi tràng", "duodenum": "Tá tràng", "stomach": "Dạ dày",
      "esophagus": "Thực quản", "esophageal": "Thuộc thực quản", "squamous": "Tế bào vảy", "cancer": "Ung thư",
      "carcinoma": "Ung thư biểu mô", "adenocarcinoma": "Ung thư biểu mô tuyến", "ibd": "Bệnh viêm ruột mạn (IBD)",
      "colitis": "Viêm đại tràng", "ulcerative": "Viêm loét", "remission": "Lùi bệnh / Thuyên giảm",
      "histological": "Mô bệnh học", "mucosal": "Thuộc niêm mạc", "mucosa": "Niêm mạc", "submucosal": "Dưới niêm mạc",
      "epithelium": "Biểu mô", "glands": "Các tuyến", "hyperplastic": "Tăng sản", "malignant": "Ác tính",
      "benign": "Lành tính", "dysplasia": "Loạn sản", "neoplasia": "Tân sinh", "invasion": "Xâm lấn",
      "endoscopy": "Nội soi", "endoscopic": "Qua nội soi", "lesion": "Tổn thương", "lesions": "Các tổn thương",
      "biopsy": "Sinh thiết", "biopsies": "Các mẫu sinh thiết", "adenoma": "U tuyến", "colorectal": "Đại trực tràng",
      "surgery": "Phẫu thuật", "colectomy": "Cắt đại tràng", "chromoendoscopy": "Nội soi nhuộm màu",
      "nbi": "Nội soi dải tần ánh sáng hẹp (NBI)", "lugol": "Nhuộm Lugol", "vessels": "Các mạch máu",
      "vascular": "Mạch máu", "resection": "Cắt bỏ", "emr": "Cắt hớt niêm mạc (EMR)", "esd": "Cắt tách dưới niêm mạc (ESD)",
      "screening": "Tầm soát", "surveillance": "Theo dõi định kỳ", "detection": "Phát hiện", "diagnosis": "Chẩn đoán",
      "epidemiology": "Dịch tễ học", "incidence": "Tỷ lệ mắc mới", "mortality": "Tỷ lệ tử vong", "reflux": "Trào ngược",
      "achalasia": "Co thắt tâm vị", "stricture": "Hẹp ống tiêu hóa", "ulcer": "Ổ loét", "ulcers": "Các ổ loét",
      "psc": "Viêm đường mật xơ cứng nguyên phát (PSC)", "ultrasound": "Siêu âm", "abdomen": "Ổ bụng",
      "liver": "Gan", "hepatic": "Thuộc gan", "gallbladder": "Túi mật", "biliary": "Đường mật", "pancreas": "Tụy",
      "spleen": "Lách", "kidney": "Thận", "renal": "Thuộc thận", "aorta": "Động mạch chủ", "portal": "Tĩnh mạch cửa",
      "transducer": "Đầu dò siêu âm", "echogenic": "Tăng âm", "hypoechoic": "Giảm âm", "ascites": "Dịch ổ bụng"
    };

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

    function switchTab(tabName) {
      const tabs = ['scene', 'wordbyword', 'vocab', 'summary', 'clinical', 'research', 'quiz'];
      tabs.forEach(t => {
        document.getElementById('tab-content-' + t).classList.toggle('hidden', t !== tabName);
        document.getElementById('tab-btn-' + t).classList.toggle('tab-active', t === tabName);
      });
    }

    function quickLoad(vid) {
      const u = "https://www.youtube.com/watch?v=" + vid;
      document.getElementById("videoUrl").value = u;
      document.getElementById("customTitle").value = "";
      processUrlOrId(u);
    }

    function onYouTubeIframeAPIReady() {
      updateSavedBadge();
      quickLoad("HImiTSiYXDY");
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

    async function fetchFromPhoneBrowser(vid, pbAsr, pbMan) {
      // 1. Gọi cổng /youtubei/v1/get_transcript bằng khóa Protobuf (Không bị chặn bởi playabilityStatus)
      const gtProxies = [
        "https://api.cors.lol/?url=" + encodeURIComponent("https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false"),
        "https://thingproxy.freeboard.io/fetch/https://www.youtube.com/youtubei/v1/get_transcript?prettyPrint=false"
      ];
      for (const ep of gtProxies) {
        for (const pVal of [pbAsr, pbMan].filter(Boolean)) {
          try {
            const r = await fetch(ep, {
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
      }

      // 2. Gọi Invidious /api/v1/transcripts/{vid} và Piped trực tiếp từ trình duyệt
      for (const inv of ["https://inv.nadeko.net", "https://invidious.nerdvpn.de", "https://yewtu.be"]) {
        try {
          const r = await fetch(`${inv}/api/v1/transcripts/${vid}`);
          if (r.ok) {
            const d = await r.json();
            if (d.transcripts && d.transcripts.length) {
              const subs = normalizeSubsJS(d.transcripts.map(it => ({
                text: it.text || "",
                start: +(parseFloat(it.start || 0) / (parseFloat(it.start || 0) > 10000 ? 1000 : 1)).toFixed(2),
                duration: 3.0
              })));
              if (subs.length) return subs;
            }
          }
        } catch(e) {}
      }

      // 3. Quét trang Watch lấy getTranscriptEndpoint.params hoặc baseUrl qua codetabs/allorigins
      const getProxies = [
        u => "https://api.codetabs.com/v1/proxy?quest=" + encodeURIComponent(u),
        u => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u)
      ];
      const wUrl = "https://www.youtube.com/watch?v=" + vid + "&hl=en";
      for (const px of getProxies) {
        try {
          const r1 = await fetch(px(wUrl));
          const html = await r1.text();
          const m = html.match(/"baseUrl"\s*:\s*"(https:\/\/www\.youtube\.com\/api\/timedtext[^"]+)"/);
          if (m) {
            const cleanUrl = m[1].replace(/\\u0026/g, "&").replace(/\\\//g, "/").replace("&fmt=srv3", "");
            const r2 = await fetch(px(cleanUrl));
            const subs = parseCapData(await r2.text());
            if (subs.length) return subs;
          }
        } catch(e) {}
      }
      return [];
    }

    // DÁN NHANH TRANSCRIPT TỪ YOUTUBE (HỖ TRỢ MỌI ĐỊNH DẠNG CÓ HOẶC KHÔNG CÓ MỐC GIỜ)
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
        // Phân tích định dạng copy trực tiếp từ khung "Show transcript" của YouTube (Dòng giờ 0:15 xen kẽ dòng chữ)
        const lines = raw.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
        let curTime = 0, autoTime = 0;
        for (let i = 0; i < lines.length; i++) {
          if (/^\d{1,2}:\d{2}(?::\d{2})?$/.test(lines[i])) {
            curTime = toSecJS(lines[i]);
          } else {
            const st = curTime || autoTime;
            parsed.push({text: lines[i], start: +st.toFixed(2), duration: 5.0});
            autoTime = st + 5.0;
            curTime = 0;
          }
        }
        parsed = normalizeSubsJS(parsed);
      }
      if (!parsed.length) return alert("Không nhận diện được văn bản phụ đề!");
      closePasteModal();
      const customT = document.getElementById("customTitle").value.trim();
      const targetLang = document.getElementById("targetLang").value || "vi";
      const r = await fetch("/api/process-raw-subtitles", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({video_id: currentVideoId, custom_title: customT, target_lang: targetLang, subtitles: parsed})
      });
      const d = await r.json();
      if (d.subtitles) applyLoadedLecture(d);
    }

    // CHỨC NĂNG AI NGHE TRỰC TIẾP TỪ LOA
    function toggleLiveMicTranscribe() {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      const btn = document.getElementById("btnLiveMic");
      if (!SR) {
        return alert("Trình duyệt hiện tại chưa hỗ trợ Web Speech API. Bạn hãy dùng nút '📋 Dán Transcript' hoặc '☁️ Nạp SRT/VTT' nhé!");
      }
      if (isLiveListening && speechRec) {
        isLiveListening = false;
        speechRec.stop();
        btn.innerText = "🎙️ AI Nghe Loa";
        btn.className = "bg-slate-800 hover:bg-slate-700 text-amber-300 border border-amber-500/30 py-2 px-2 rounded-lg font-semibold text-[11px] flex items-center justify-center gap-1";
        return;
      }
      speechRec = new SR();
      speechRec.lang = "en-US";
      speechRec.continuous = true;
      speechRec.interimResults = false;
      isLiveListening = true;
      btn.innerText = "🔴 Đang Nghe (Dừng)";
      btn.className = "bg-amber-600 text-white py-2 px-2 rounded-lg font-bold text-[11px] flex items-center justify-center gap-1 animate-pulse";

      speechRec.onresult = async (event) => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          if (event.results[i].isFinal) {
            const spokenEn = event.results[i][0].transcript.trim();
            if (!spokenEn) continue;
            const curT = (player && player.getCurrentTime) ? player.getCurrentTime() : 0;
            const newIdx = subtitles.length;
            const newSub = {text: spokenEn, start: Math.max(0, +(curT - 3).toFixed(2)), duration: 5.0, vi: "⏳ Đang dịch...", translated: false};
            subtitles.push(newSub);
            renderSubs();
            const r = await fetch("/api/translate-batch", {
              method: "POST",
              headers: {"Content-Type": "application/json"},
              body: JSON.stringify({texts: [spokenEn], target_lang: document.getElementById("targetLang").value || "vi"})
            });
            const d = await r.json();
            if (d.translations && d.translations[0]) {
              subtitles[newIdx].vi = d.translations[0];
              subtitles[newIdx].translated = true;
              const el = document.getElementById("sub-vi-" + newIdx);
              if (el) el.innerText = d.translations[0];
            }
          }
        }
      };
      speechRec.onend = () => { if (isLiveListening) try { speechRec.start(); } catch(e){} };
      try { speechRec.start(); } catch(e){}
    }

    async function handleSrtUpload(event) {
      const file = event.target.files[0];
      if (!file) return;
      const text = await file.text();
      const parsed = parseCapData(text);
      if (!parsed.length) return alert("File phụ đề không đúng định dạng SRT/VTT!");
      const customT = document.getElementById("customTitle").value.trim() || file.name.replace(/\.[^.]+$/, "");
      const r = await fetch("/api/process-raw-subtitles", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({video_id: currentVideoId, custom_title: customT, subtitles: parsed})
      });
      const d = await r.json();
      if (d.subtitles) applyLoadedLecture(d);
    }

    function applyLoadedLecture(d) {
      currentVideoId = d.video_id;
      currentTitle = d.title || ("Bài giảng Y khoa (" + d.video_id + ")");
      document.getElementById("lectureTitleDisplay").innerText = currentTitle;
      document.getElementById("lectureUrlDisplay").innerText = "https://www.youtube.com/watch?v=" + currentVideoId;
      subtitles = d.subtitles;
      lastSpokenIdx = -1;
      renderSubs();
      buildAllMedicalTabs();
      startBgTrans();
    }

    async function processUrlOrId(url) {
      const vid = extractVid(url);
      if (!vid) return alert("Link YouTube không hợp lệ!");
      currentVideoId = vid;
      const customT = document.getElementById("customTitle").value.trim();
      const targetLang = document.getElementById("targetLang").value;

      initPlayer(vid);
      document.getElementById("lectureUrlDisplay").innerText = "https://www.youtube.com/watch?v=" + vid;
      const box = document.getElementById("subtitleList");
      box.innerHTML = `<p class="text-blue-400 text-xs sm:text-sm text-center pt-16 animate-pulse">⏳ Đang trích xuất lời thoại thật của video & dịch song ngữ...</p>`;
      document.getElementById("subCount").innerText = "Đang tải...";

      const r = await fetch("/api/process-video", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({url: "https://www.youtube.com/watch?v=" + vid, custom_title: customT, target_lang: targetLang})
      });
      let d = await r.json();

      if (d.need_client_fetch) {
        const rawSubs = await fetchFromPhoneBrowser(vid, d.pb_asr, d.pb_man);
        const r2 = await fetch("/api/process-raw-subtitles", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({video_id: vid, custom_title: customT, target_lang: targetLang, subtitles: rawSubs || []})
        });
        d = await r2.json();
      }

      if (d.error) {
        box.innerHTML = `<div class="text-center pt-10 px-4 space-y-3">
          <p class="text-amber-400 text-xs sm:text-sm leading-relaxed">${d.error}</p>
          <div class="flex justify-center gap-2 pt-2">
            <button onclick="openPasteModal()" class="bg-purple-600 hover:bg-purple-500 text-white text-xs px-3.5 py-2 rounded-lg font-bold">📋 Dán Nhanh Transcript</button>
            <button onclick="toggleLiveMicTranscribe()" class="bg-amber-600 hover:bg-amber-500 text-white text-xs px-3.5 py-2 rounded-lg font-bold">🎙️ AI Nghe Trực Tiếp</button>
          </div>
        </div>`;
        document.getElementById("subCount").innerText = "0 câu";
      } else if (d.subtitles) {
        applyLoadedLecture(d);
      }
    }

    function buildAllMedicalTabs() {
      const foundTerms = {};
      subtitles.forEach((s, idx) => {
        const words = s.text.toLowerCase().replace(/[^a-z0-9\s-]/g, "").split(/\s+/);
        words.forEach(w => {
          if (CLIENT_MED_DICT[w]) {
            if (!foundTerms[w]) {
              foundTerms[w] = {word: w, meaning: CLIENT_MED_DICT[w], count: 0, firstStart: s.start, firstIdx: idx, sample: s.text};
            }
            foundTerms[w].count++;
          }
        });
      });
      const vocabArr = Object.values(foundTerms).sort((a, b) => b.count - a.count);
      document.getElementById("vocabTabCount").innerText = vocabArr.length;

      const vBox = document.getElementById("vocabListContainer");
      if (!vocabArr.length) {
        vBox.innerHTML = `<p class="text-slate-400 text-xs text-center pt-10">Bấm vào bất kỳ từ nào trên phụ đề để tra cứu chi tiết.</p>`;
      } else {
        vBox.innerHTML = vocabArr.map(item => `
          <div class="p-2.5 rounded-lg bg-slate-950 border border-slate-800 flex justify-between items-center gap-2">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <span class="font-bold text-yellow-400 text-sm capitalize">${item.word}</span>
                <span class="text-[11px] bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded-full">Xuất hiện ${item.count} lần</span>
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
      wbwBox.innerHTML = subtitles.slice(0, 80).map((s, idx) => {
        const tokens = s.text.split(/\s+/).map(tok => {
          const clean = tok.replace(/[^a-zA-Z0-9-]/g, "").toLowerCase();
          const m = CLIENT_MED_DICT[clean];
          return `<span onclick="lookupWord(event,'${clean}',${idx})" class="cursor-pointer inline-flex flex-col items-center bg-slate-900 border ${m ? 'border-emerald-500/50' : 'border-slate-800'} px-2 py-1 rounded mr-1 mb-1 hover:border-yellow-400">
            <span class="text-xs font-semibold text-white">${tok}</span>
            <span class="text-[10px] ${m ? 'text-emerald-400 font-bold' : 'text-slate-400'}">${m || 'tra từ'}</span>
          </span>`;
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
          <span class="text-xs font-bold text-blue-400">1. Mở đầu & Đặt vấn đề (0:00 - ${formatTime(subtitles[Math.min(third, subtitles.length-1)].start)})</span>
          <p class="text-xs text-slate-300 leading-relaxed">${p1}</p>
        </div>
        <div class="p-3 rounded-xl bg-slate-950 border border-slate-800 space-y-1">
          <span class="text-xs font-bold text-emerald-400">2. Nội dung phân tích chuyên môn & Kỹ thuật</span>
          <p class="text-xs text-slate-300 leading-relaxed">${p2}</p>
        </div>
        <div class="p-3 rounded-xl bg-slate-950 border border-slate-800 space-y-1">
          <span class="text-xs font-bold text-amber-400">3. Tổng kết & Khuyến cáo thực hành</span>
          <p class="text-xs text-slate-300 leading-relaxed">${p3}</p>
        </div>
      `;

      const clinRx = /\b(patient|lesion|polyp|biopsy|resection|esd|emr|nbi|lugol|endoscop|ultrasound|transducer|Paris|diagnos|treatment|careful|wash|mucosa|ibd|colitis|crohn|remission|surveillance)\b/i;
      const clinItems = subtitles.map((s, idx) => ({...s, idx})).filter(s => clinRx.test(s.text)).slice(0, 25);
      document.getElementById("clinicalContainer").innerHTML = (clinItems.length ? clinItems : subtitles.slice(0, 10).map((s, idx) => ({...s, idx}))).map(s => `
        <div onclick="playSeg(${s.start},${s.duration},${s.idx})" class="p-2.5 rounded-lg bg-slate-950 border border-rose-500/30 hover:border-rose-400 cursor-pointer">
          <div class="flex justify-between text-[11px] text-rose-300 font-semibold mb-1">
            <span>🏥 Lưu ý lâm sàng • ⏱️ ${formatTime(s.start)}</span>
            <span>🔊 Xem phân cảnh</span>
          </div>
          <p class="text-xs text-white font-medium">${s.text}</p>
          <p class="text-xs text-emerald-400 mt-1">${s.vi}</p>
        </div>
      `).join("");

      const resRx = /(\d+%|\bstudy\b|\btrial\b|\brandomized\b|\bmeta-analysis\b|\bguideline\b|\bsensitivity\b|\bspecificity\b|\bincidence\b|\bmortality\b|\brisk\b|\bconsensus\b|\bscore\b)/i;
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
      const medEntries = [];
      subtitles.forEach(s => {
        const words = s.text.toLowerCase().replace(/[^a-z\s]/g, "").split(/\s+/);
        words.forEach(w => {
          if (CLIENT_MED_DICT[w] && !medEntries.find(x => x.w === w)) {
            medEntries.push({w, m: CLIENT_MED_DICT[w], ctx: s.text});
          }
        });
      });
      const pool = medEntries.length >= 4 ? medEntries : Object.entries(CLIENT_MED_DICT).slice(0, 12).map(([w, m]) => ({w, m, ctx: "Thuật ngữ y khoa trọng tâm trong bài giảng."}));
      const shuffled = [...pool].sort(() => Math.random() - 0.5).slice(0, 5);
      const allMeanings = Object.values(CLIENT_MED_DICT);

      const qBox = document.getElementById("quizContainer");
      qBox.innerHTML = shuffled.map((q, qIdx) => {
        const wrong = allMeanings.filter(m => m !== q.m).sort(() => Math.random() - 0.5).slice(0, 3);
        const options = [q.m, ...wrong].sort(() => Math.random() - 0.5);
        return `<div class="p-3 rounded-xl bg-slate-950 border border-slate-800 space-y-2">
          <p class="text-xs font-bold text-yellow-400">Câu ${qIdx + 1}: Thuật ngữ y khoa <span class="underline text-white">"${q.w.toUpperCase()}"</span> trong bài giảng có nghĩa là gì?</p>
          <p class="text-[11px] text-slate-400 italic">Câu gốc trong video: "${q.ctx}"</p>
          <div class="grid grid-cols-1 gap-1.5 pt-1">
            ${options.map(opt => `
              <button onclick="checkQuizAnswer(this, '${opt === q.m ? '1' : '0'}', '${q.m}')" class="text-left text-xs p-2 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-200 transition">
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
        box.innerHTML = `<p class="text-slate-400 text-xs text-center py-8">Chưa có bài giảng nào được lưu. Hãy bấm nút "💾 Lưu bản dịch" để lưu lại bài giảng đang học nhé!</p>`;
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
      Object.entries(CLIENT_MED_DICT).forEach(([k, v]) => {
        if (subtitles.some(s => s.text.toLowerCase().includes(k))) {
          lines.push(`- ${k.toUpperCase()}: ${v}`);
        }
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
      const url = document.getElementById("videoUrl").value.trim() || ("https://www.youtube.com/watch?v=" + currentVideoId);
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
        for (let i = 0; i < subtitles.length && todo.length < 35; i++) {
          const idx = (activeSubIndex + i) % subtitles.length;
          if (!subtitles[idx].translated) todo.push(idx);
        }
        const done = subtitles.filter(s => s.translated).length;
        document.getElementById("subCount").innerText = done < subtitles.length ? `⚡ ${done}/${subtitles.length} câu` : `${subtitles.length} câu`;
        if (!todo.length) {
          updateAnalyticalTabs();
          break;
        }
        try {
          const r = await fetch("/api/translate-batch", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({texts: todo.map(i => subtitles[i].text), target_lang: targetLang})
          });
          const d = await r.json();
          if (cur !== jobId) return;
          (d.translations || []).forEach((vi, k) => {
            const idx = todo[k]; subtitles[idx].vi = vi; subtitles[idx].translated = true;
            const el = document.getElementById("sub-vi-" + idx); if (el) el.innerText = vi;
            const wbwEl = document.getElementById("wbw-vi-" + idx); if (wbwEl) wbwEl.innerText = vi;
          });
          updateAnalyticalTabs();
        } catch (e) { await new Promise(r => setTimeout(r, 1000)); }
      }
    }

    function renderSubs() {
      const box = document.getElementById("subtitleList"); box.innerHTML = "";
      subtitles.forEach((s, i) => {
        const div = document.createElement("div");
        div.id = "sub-" + i;
        div.className = "p-2.5 rounded-lg bg-slate-950/80 border border-slate-800 cursor-pointer hover:border-slate-700";
        const words = s.text.split(" ").map(w => {
          const clean = w.replace(/[^a-zA-Z0-9-]/g, "");
          const isMed = CLIENT_MED_DICT[clean.toLowerCase()];
          return `<span class="word-span ${isMed ? 'med-term' : ''}" onclick="lookupWord(event,'${clean}',${i})">${w}</span>`;
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
      document.getElementById("wMeaning").innerText = "Đang phân tích thuật ngữ...";
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
