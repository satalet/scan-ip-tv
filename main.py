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
    line_count = 0
    max_items = 1000

    for raw_line in response_iterator:
        try:
            line = raw_line.decode('utf-8', errors='ignore').strip()
        except:
            continue

        if not line:
            continue

        line_count += 1
        if line.startswith("#EXTINF:"):
            name_match = re.search(r',([^,]+)$', line)
            name = name_match.group(1).strip() if name_match else "قناة بدون اسم"

            group_match = re.search(r'group-title="([^"]+)"', line, re.IGNORECASE)
            group = group_match.group(1).strip() if group_match else "عام"

            logo_match = re.search(r'tvg-logo="([^"]+)"', line, re.IGNORECASE)
            logo = logo_match.group(1).strip() if logo_match else ""

            current_item = {"name": name, "group": group, "logo": logo, "url": ""}
        elif current_item and not line.startswith("#"):
            current_item["url"] = line
            url_lower = line.lower()

            if "/series/" in url_lower:
                if len(series) < max_items:
                    series.append(current_item)
            elif "/movie/" in url_lower or url_lower.endswith(".mp4") or url_lower.endswith(".mkv"):
                if len(movies) < max_items:
                    movies.append(current_item)
            else:
                if len(live) < max_items:
                    live.append(current_item)

            current_item = None

        if line_count > 40000:
            break

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
        r = requests.get(url, headers=headers, timeout=15, verify=False, stream=True)
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
