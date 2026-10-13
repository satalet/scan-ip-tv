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

def extract_with_gemini(image_bytes, mime_type="image/jpeg"):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None, "GEMINI_API_KEY غير موجود في السيرفر."

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        prompt = (
            "Extract IPTV credentials from this image. "
            "Return ONLY a raw JSON object with keys: 'host', 'port', 'user', 'pass'. "
            "Do not include markdown blocks, notes, or backticks. "
            "Example format: {\"host\":\"domain.com\",\"port\":8080,\"user\":\"username\",\"pass\":\"password\"}. "
            "Clean the host from http:// or https://. The port should be an integer or string of digits."
        )

        last_error = None
        for model_name in AI_MODELS:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        prompt
                    ]
                )
                raw_text = response.text.strip()
                raw_text = re.sub(r'^```json\s*', '', raw_text)
                raw_text = re.sub(r'\s*```$', '', raw_text)
                data = json.loads(raw_text)
                return data, None
            except Exception as e:
                last_error = str(e)
                continue

        return None, f"فشلت جميع النماذج الاحتياطية: {last_error}"
    except Exception as e:
        return None, str(e)

def parse_m3u_stream(response_iterator):
    live = []
    movies = []
    series = []

    current_item = None
    line_count = 0
    max_items_per_category = 1000  # حماية الذاكرة ومنع Crash السيرفر

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

            current_item = {
                "name": name,
                "group": group,
                "logo": logo,
                "url": ""
            }
        elif current_item and not line.startswith("#"):
            current_item["url"] = line
            url_lower = line.lower()

            if "/series/" in url_lower:
                if len(series) < max_items_per_category:
                    series.append(current_item)
            elif "/movie/" in url_lower or url_lower.endswith(".mp4") or url_lower.endswith(".mkv"):
                if len(movies) < max_items_per_category:
                    movies.append(current_item)
            else:
                if len(live) < max_items_per_category:
                    live.append(current_item)

            current_item = None

        # سقف أمان للأداء في الاستضافة المجانية
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

    headers = {
        "User-Agent": "IPTVSmartersPro",
        "Accept": "*/*"
    }

    try:
        # استخدام stream=True لمنع تحميل الملف كاملاً في RAM دفعة واحدة
        r = requests.get(url, headers=headers, timeout=15, verify=False, stream=True)
        if r.status_code != 200:
            return jsonify({"success": False, "message": f"فشل مزود IPTV في إرجاع القائمة (كود {r.status_code})"}), 400

        parsed_data = parse_m3u_stream(r.iter_lines())
        return jsonify({
            "success": True,
            "data": parsed_data
        })
    except requests.exceptions.Timeout:
        return jsonify({"success": False, "message": "انتهت مهلة الاتصال مع سيرفر الـ IPTV الأصلي (بطيء أو لا يستجيب)."}), 504
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

    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception:
        return jsonify({"success": False, "message": "تنسيق الصورة غير صالح."}), 400

    creds, err = extract_with_gemini(image_bytes, mime_type)
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
