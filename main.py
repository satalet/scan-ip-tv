import os
import re
import json
import base64
import socket
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import requests
import urllib3
from flask import Flask, request, jsonify
from flask_cors import CORS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

COMMON_PORTS = [80, 443, 8000, 8080, 8880, 2082, 2086, 2095, 25461]

AI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview"
]

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, PUT, DELETE'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, x-goog-api-key'
    return response

def check_port(ip, port, timeout=1.8):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip, port)) == 0
    except:
        return False

def extract_with_gemini_rest(image_b64, mime_type="image/jpeg"):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY غير موجود في السيرفر."

    prompt = (
        "Extract IPTV credentials from this image. "
        "Return ONLY a raw JSON object with keys: 'host', 'port', 'user', 'pass'. "
        "Do not include markdown blocks, notes, or backticks. "
        "Example format: {\"host\":\"domain.com\",\"port\":8080,\"user\":\"username\",\"pass\":\"password\"}. "
        "Clean the host from http:// or https://. The port should be an integer or string of digits."
    )

    payload = {
        "contents": [{
            "parts": [
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": image_b64
                    }
                },
                {"text": prompt}
            ]
        }]
    }

    last_error = None
    for model_name in AI_MODELS:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=12)
            res_json = res.json()

            if res.status_code == 200:
                raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                raw_text = re.sub(r'^```json\s*', '', raw_text)
                raw_text = re.sub(r'\s*```$', '', raw_text)
                data = json.loads(raw_text)
                return data, None
            else:
                err_msg = res_json.get("error", {}).get("message", f"Status {res.status_code}")
                last_error = f"{model_name}: {err_msg}"
                continue
        except Exception as e:
            last_error = f"{model_name}: {str(e)}"
            continue

    return None, f"فشلت جميع النماذج الاحتياطية: {last_error}"

def parse_m3u_stream(response_iterator):
    live = []
    movies = []
    series = []
    current_item = None
    MAX_PER_TAB = 2500

    for raw_line in response_iterator:
        try:
            line = raw_line.decode('utf-8', errors='ignore').strip()
        except:
            continue

        if not line:
            continue

        if line.startswith("#EXTINF:"):
            comma_idx = line.rfind(',')
            name = line[comma_idx + 1:].strip() if comma_idx != -1 else "قناة بدون اسم"

            group = "عام"
            if 'group-title="' in line:
                s = line.split('group-title="', 1)[1]
                group = s.split('"', 1)[0].strip() or "عام"
            elif "group-title='" in line:
                s = line.split("group-title='", 1)[1]
                group = s.split("'", 1)[0].strip() or "عام"

            logo = ""
            if 'tvg-logo="' in line:
                s = line.split('tvg-logo="', 1)[1]
                logo = s.split('"', 1)[0].strip()
            elif "tvg-logo='" in line:
                s = line.split("tvg-logo='", 1)[1]
                logo = s.split("'", 1)[0].strip()

            current_item = {"name": name, "group": group, "logo": logo, "url": ""}
        elif current_item and not line.startswith("#"):
            current_item["url"] = line
            url_lower = line.lower()
            grp_lower = current_item["group"].lower()

            is_series = ("/series/" in url_lower) or ("series" in grp_lower) or ("مسلسل" in grp_lower) or ("season" in grp_lower)
            is_movie = ("/movie/" in url_lower) or url_lower.endswith((".mp4", ".mkv", ".avi")) or ("movie" in grp_lower) or ("فلم" in grp_lower) or ("فيلم" in grp_lower)

            if is_series:
                if len(series) < MAX_PER_TAB:
                    series.append(current_item)
            elif is_movie:
                if len(movies) < MAX_PER_TAB:
                    movies.append(current_item)
            else:
                if len(live) < MAX_PER_TAB:
                    live.append(current_item)

            current_item = None

    return {"live": live, "movies": movies, "series": series}

def verify_and_build_response(host, port, user, pwd):
    port = int(port) if str(port).isdigit() else 8080

    try:
        ip = socket.gethostbyname(host)
    except Exception:
        return jsonify({"success": False, "message": f"خطأ DNS: تعذر الوصول إلى الدومين ({host})"}), 400

    working_port = port
    if not check_port(ip, port):
        found = [p for p in COMMON_PORTS if p != port and check_port(ip, p, 1.2)]
        if found:
            working_port = found[0]
        else:
            return jsonify({"success": False, "message": f"السيرفر {host} مغلق (المنفذ {port} لا يستجيب)"}), 400

    protocol = "https" if working_port == 443 else "http"
    base_url = f"{protocol}://{host}:{working_port}" if working_port not in [80, 443] else f"{protocol}://{host}"
    api_url = f"{base_url}/player_api.php"
    headers = {"User-Agent": "IPTVSmartersPro"}

    t0 = time.time()
    try:
        res = requests.get(api_url, params={"username": user, "password": pwd}, headers=headers, timeout=6, verify=False)
        latency = round((time.time() - t0) * 1000, 1)

        if res.status_code == 200:
            res_data = res.json()
            user_info = res_data.get("user_info", {})
            if user_info.get("auth", 0) == 1 or user_info.get("status") == "Active":
                exp_date_raw = user_info.get("exp_date")
                exp_date = "غير محدد"
                if exp_date_raw:
                    try:
                        exp_date = datetime.fromtimestamp(int(exp_date_raw)).strftime('%Y-%m-%d')
                    except: pass

                full_m3u = f"{base_url}/get.php?username={user}&password={pwd}&type=m3u_plus&output=m3u8"
                return jsonify({
                    "success": True,
                    "status": "Active",
                    "host": host,
                    "port": working_port,
                    "user": user,
                    "pass": pwd,
                    "exp_date": exp_date,
                    "latency": f"{latency} ms",
                    "url": full_m3u
                })
            else:
                return jsonify({"success": False, "message": "السيرفر متصل ولكن الحساب غير فعال أو كلمة المرور خطأ."})
        elif res.status_code == 404:
            return jsonify({"success": False, "message": "الحساب منتهي الصلاحية أو تمويه 404."})
        else:
            return jsonify({"success": False, "message": f"رمز الاستجابة: {res.status_code}"})
    except Exception as e:
        return jsonify({"success": False, "message": f"فشل الاتصال بـ API: {str(e)}"})

@app.route('/', methods=['GET', 'OPTIONS'])
def home():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200
    return jsonify({
        "service": "Albasem-TV Engine",
        "status": "running",
        "ai": bool(os.environ.get("GEMINI_API_KEY")),
        "fallback_models": AI_MODELS
    })

@app.route('/api/check', methods=['POST', 'OPTIONS'])
def check_text():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200

    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"success": False, "message": "لم يتم إرسال نص للفحص."}), 400

    host, port, user, pwd = None, 8080, None, None

    if "http://" in text or "https://" in text:
        url_match = re.search(r'https?://[^\s]+', text)
        if url_match:
            full_url = url_match.group(0)
            parsed = urlparse(full_url)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            qs = parse_qs(parsed.query)
            if 'username' in qs: user = qs['username'][0]
            if 'password' in qs: pwd = qs['password'][0]

    if not (host and user and pwd):
        h_m = re.search(r'(?:host|server|سيرفر)[\s:=]+([a-zA-Z0-9.-]+)', text, re.I)
        p_m = re.search(r'(?:port|بورت)[\s:=]+(\d+)', text, re.I)
        u_m = re.search(r'(?:user|username|مستخدم)[\s:=]+([a-zA-Z0-9._-]+)', text, re.I)
        w_m = re.search(r'(?:pass|password|مرور)[\s:=]+([a-zA-Z0-9._-]+)', text, re.I)

        if h_m: host = h_m.group(1)
        if p_m: port = int(p_m.group(1))
        if u_m: user = u_m.group(1)
        if w_m: pwd = w_m.group(1)

    if not host or not user or not pwd:
        return jsonify({"success": False, "message": "تعذر استخراج بيانات الاشتراك (Host/User/Pass) من النص المدخل."}), 400

    return verify_and_build_response(host, port, user, pwd)


@app.route("/api/proxy")
def stream_proxy():
    target_url = request.args.get("url")
    if not target_url:
        return "Missing URL", 400

    headers = {"User-Agent": "IPTVSmartersPro"}
    try:
        req = requests.get(target_url, headers=headers, stream=True, timeout=15, verify=False)
        content_type = req.headers.get("Content-Type", "")

        if "mpegurl" in content_type or target_url.endswith(".m3u8") or ".m3u8?" in target_url:
            parsed = urlparse(target_url)
            base_domain = f"{parsed.scheme}://{parsed.netloc}"
            parent_path = target_url.rsplit("/", 1)[0]

            rewritten = []
            for raw_l in req.text.splitlines():
                l = raw_l.strip()
                if l and not l.startswith("#"):
                    if l.startswith("http://") or l.startswith("https://"):
                        full_ts = l
                    elif l.startswith("/"):
                        full_ts = base_domain + l
                    else:
                        full_ts = parent_path + "/" + l
                    rewritten.append(f"https://scan-ip-tv.onrender.com/api/proxy?url={quote(full_ts, safe="")}")
                else:
                    rewritten.append(l)

            resp = app.response_class("\n".join(rewritten), status=200, content_type="application/vnd.apple.mpegurl")
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp

        def generate():
            for chunk in req.iter_content(chunk_size=16384):
                if chunk:
                    yield chunk

        resp = app.response_class(generate(), status=req.status_code, content_type=content_type or "video/mp2t")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        return f"Proxy Error: {str(e)}", 500


@app.route('/api/parse', methods=['POST', 'OPTIONS'])
def parse_playlist():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200

    data = request.get_json(silent=True) or {}
    url = data.get("url") or request.args.get("url")
    if not url:
        return jsonify({"success": False, "message": "يرجى تزويد رابط التشغيل (url)"}), 400

    headers = {"User-Agent": "IPTVSmartersPro", "Accept": "*/*"}
    try:
        r = requests.get(url, headers=headers, timeout=60, verify=False, stream=True)
        if r.status_code != 200:
            return jsonify({"success": False, "message": f"فشل مزود IPTV (كود {r.status_code})"}), 400

        parsed_data = parse_m3u_stream(r.iter_lines())
        return jsonify({"success": True, "data": parsed_data})
    except Exception as e:
        return jsonify({"success": False, "message": f"تعذر جلب القائمة: {str(e)}"}), 500

@app.route('/api/ai-scan', methods=['POST', 'OPTIONS'])
def ai_scan():
    if request.method == 'OPTIONS':
        return jsonify({"status": "ok"}), 200

    data = request.get_json(silent=True) or {}
    image_b64 = data.get("image", "")
    mime_type = data.get("mime_type", "image/jpeg")

    if not image_b64:
        return jsonify({"success": False, "message": "لم يتم إرسال الصورة."}), 400

    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]

    creds, err = extract_with_gemini_rest(image_b64, mime_type)
    if err or not creds:
        return jsonify({"success": False, "message": f"فشل استخراج الذكاء الاصطناعي: {err}"}), 500

    host = creds.get("host")
    port = creds.get("port", "8080")
    user = creds.get("user")
    pwd = creds.get("pass")

    if not host or not user or not pwd:
        return jsonify({"success": False, "message": "البيانات المستخرجة من الصورة ناقصة."}), 400

    return verify_and_build_response(host, port, user, pwd)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
