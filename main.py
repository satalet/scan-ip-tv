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

# قائمة النماذج بالترتيب: رئيسي ثم الاحتياط الأول ثم الثاني
AI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview"
]

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
                print(f"⏳ تجربة الاستخراج بواسطة النموذج: {model_name}...")
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
                print(f"✅ نجح الاستخراج باستخدام: {model_name}")
                return data, None
            except Exception as e:
                print(f"⚠️ فشل الموديل {model_name} بسبب: {e}، جاري تجربة الموديل البديل...")
                last_error = str(e)
                continue

        return None, f"فشلت جميع النماذج الاحتياطية. آخر خطأ: {last_error}"
    except Exception as e:
        return None, str(e)

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

@app.route('/')
def home():
    return jsonify({
        "service": "Albasem-TV Engine",
        "status": "running",
        "ai": bool(os.environ.get("GEMINI_API_KEY")),
        "fallback_models": AI_MODELS
    })

@app.route('/api/ai-scan', methods=['POST'])
def ai_scan():
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
