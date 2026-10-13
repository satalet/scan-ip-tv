import os
import re
import socket
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import requests
import urllib3
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

COMMON_PORTS = [80, 443, 8000, 8080, 8880, 2082, 2086, 2095, 25461]

def check_port(ip, port, timeout=1.8):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip, port)) == 0
    except:
        return False

def clean_ocr_text(text):
    # تصحيح الأخطاء الشائعة في قراءة الـ OCR
    t = text.replace(".xy2", ".xyz").replace(".XY2", ".xyz")
    t = t.replace(".Xy2", ".xyz")
    return t

def extract_credentials(text):
    cleaned = clean_ocr_text(re.sub(r':contentReference\[.*?\](?:\{.*?\})?', '', text).strip())
    host, port, user, pwd = None, "8080", None, None

    # 1. صيد الهوست والبورت بأشكال متعددة حتى مع تشوهات OCR
    domain_match = re.search(
        r'(?:https?://)?([a-zA-Z0-9.-]+\.[a-zA-Z0-9]{2,10})[:\s]+(\d{2,5})',
        cleaned, re.IGNORECASE
    )
    if domain_match:
        cand_h = domain_match.group(1).strip()
        cand_p = domain_match.group(2).strip()
        # تنظيف الرموز الزائدة قبل الهوست
        cand_h = re.sub(r'^[^a-zA-Z0-9]+', '', cand_h)
        if "." in cand_h and not any(bad in cand_h.lower() for bad in ["image", "logo", "application"]):
            host = cand_h
            port = cand_p

    # 2. فحص النمط التقليدي (User / Pass)
    user_match = re.search(r'(?:User(?:name)?|Account|يوزر|المستخدم)\s*[:=]\s*([a-zA-Z0-9_-]{3,35})', cleaned, re.IGNORECASE)
    if user_match: user = user_match.group(1).strip()

    pass_match = re.search(r'(?:Pass(?:word)?|PWD|باسورد|المرور)\s*[:=]\s*([a-zA-Z0-9_-]{3,35})', cleaned, re.IGNORECASE)
    if pass_match: pwd = pass_match.group(1).strip()

    # 3. استخراج المعرفات في حال غياب المفاتيح الصريحة
    if host and (not user or not pwd):
        lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
        host_line_idx = -1
        for idx, line in enumerate(lines):
            if host in line or port in line:
                host_line_idx = idx
                break

        tokens = []
        target_lines = lines[host_line_idx + 1:] if host_line_idx != -1 else lines
        for l in target_lines:
            words = re.findall(r'[a-zA-Z0-9_-]{5,35}', l)
            for w in words:
                w_l = w.lower()
                if not any(ign in w_l for ign in [
                    "expire", "expires", "date", "http", "live", "movie",
                    "series", "stream", "xtream", "application", "zohir", "hamazo"
                ]):
                    tokens.append(w)

        if len(tokens) >= 1 and not user: user = tokens[0]
        if len(tokens) >= 2 and not pwd: pwd = tokens[1]

    return host, port or "8080", user, pwd

@app.route('/')
def home():
    return jsonify({"service": "Albasem-TV Engine", "status": "running"})

@app.route('/api/check', methods=['POST'])
def check_server():
    try:
        data = request.get_json(silent=True) or {}
        text = data.get("text", "")
        host = data.get("host")
        port = data.get("port")
        user = data.get("user")
        pwd = data.get("pass")

        if text and (not host or not user or not pwd):
            h, p, u, pw = extract_credentials(text)
            host = host or h
            port = port or p
            user = user or u
            pwd = pwd or pw

        if not host or not user or not pwd:
            return jsonify({"success": False, "message": "لم يتم العثور على بيانات سيرفر كافية."}), 400

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
        res = requests.get(api_url, params={"username": user, "password": pwd}, headers=headers, timeout=6, verify=False)
        latency = round((time.time() - t0) * 1000, 1)

        if res.status_code == 200:
            try:
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
            except Exception:
                return jsonify({"success": False, "message": "استجابة السيرفر غير متوقعة."})
        elif res.status_code == 404:
            return jsonify({"success": False, "message": "الحساب منتهي الصلاحية أو تمويه 404."})
        else:
            return jsonify({"success": False, "message": f"رمز الاستجابة: {res.status_code}"})

    except Exception as e:
        return jsonify({"success": False, "message": f"خطأ داخلي في الخادم: {str(e)}"}), 500

def fetch_category_map(api_url, user, pwd, action, headers):
    cat_map = {}
    try:
        r = requests.get(api_url, params={"username": user, "password": pwd, "action": action}, headers=headers, timeout=10, verify=False)
        if r.status_code == 200 and isinstance(r.json(), list):
            for c in r.json():
                c_id = str(c.get("category_id", ""))
                c_name = c.get("category_name", "").strip()
                if c_id and c_name: cat_map[c_id] = c_name
    except: pass
    return cat_map

@app.route('/api/parse', methods=['POST'])
def parse_iptv():
    payload = request.get_json(silent=True) or {}
    url = payload.get("url", "").strip()
    if not url: return jsonify({"success": False, "message": "الرابط فارغ"}), 400

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    elif url.startswith("https://") and any(p in url for p in [":8080", ":8000", ":8880", ":25461", ":80"]):
        url = url.replace("https://", "http://", 1)

    headers = {"User-Agent": "IPTVSmartersPro", "Accept": "*/*"}
    parsed_url = urlparse(url)
    qs = parse_qs(parsed_url.query)
    result_data = {"live": [], "movies": [], "series": []}

    if "username" in qs and "password" in qs and ("get.php" in url or "player_api.php" in url):
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        user = qs["username"][0]
        pwd = qs["password"][0]
        api_url = f"{base_url}/player_api.php"

        try:
            live_cats = fetch_category_map(api_url, user, pwd, "get_live_categories", headers)
            vod_cats = fetch_category_map(api_url, user, pwd, "get_vod_categories", headers)
            series_cats = fetch_category_map(api_url, user, pwd, "get_series_categories", headers)

            r_live = requests.get(api_url, params={"username": user, "password": pwd, "action": "get_live_streams"}, headers=headers, timeout=15, verify=False)
            if r_live.status_code == 200 and isinstance(r_live.json(), list):
                for item in r_live.json():
                    s_id = item.get("stream_id")
                    cat_id = str(item.get("category_id", ""))
                    result_data["live"].append({
                        "name": item.get("name", "قناة"),
                        "logo": item.get("stream_icon", ""),
                        "group": live_cats.get(cat_id, "عام"),
                        "url": f"{base_url}/live/{user}/{pwd}/{s_id}.ts"
                    })

            r_vod = requests.get(api_url, params={"username": user, "password": pwd, "action": "get_vod_streams"}, headers=headers, timeout=15, verify=False)
            if r_vod.status_code == 200 and isinstance(r_vod.json(), list):
                for item in r_vod.json():
                    s_id = item.get("stream_id")
                    cat_id = str(item.get("category_id", ""))
                    ext = item.get("container_extension", "mp4")
                    result_data["movies"].append({
                        "name": item.get("name", "فيلم"),
                        "logo": item.get("stream_icon", ""),
                        "group": vod_cats.get(cat_id, "أفلام"),
                        "url": f"{base_url}/movie/{user}/{pwd}/{s_id}.{ext}"
                    })

            r_series = requests.get(api_url, params={"username": user, "password": pwd, "action": "get_series"}, headers=headers, timeout=15, verify=False)
            if r_series.status_code == 200 and isinstance(r_series.json(), list):
                for item in r_series.json():
                    s_id = item.get("series_id")
                    cat_id = str(item.get("category_id", ""))
                    result_data["series"].append({
                        "name": item.get("name", "مسلسل"),
                        "logo": item.get("cover", ""),
                        "group": series_cats.get(cat_id, "مسلسلات"),
                        "url": f"{base_url}/series/{user}/{pwd}/{s_id}"
                    })

            return jsonify({"success": True, "data": result_data})
        except: pass

    try:
        r = requests.get(url, headers=headers, timeout=35, verify=False)
        r.encoding = 'utf-8'
        lines = r.text.splitlines()
        curr_item = None
        for line in lines:
            line = line.strip()
            if not line: continue
            if line.startswith("#EXTINF:"):
                name_match = re.search(r',([^,]+)$', line)
                name = name_match.group(1).strip() if name_match else "بدون اسم"
                logo_match = re.search(r'tvg-logo="([^"]+)"', line)
                logo = logo_match.group(1).strip() if logo_match else ""
                group_match = re.search(r'group-title="([^"]+)"', line)
                group = group_match.group(1).strip() if group_match else "عام"
                curr_item = {"name": name, "logo": logo, "group": group}
            elif not line.startswith("#") and curr_item:
                curr_item["url"] = line
                u_low = line.lower()
                g_low = curr_item.get("group", "").lower()
                if "/series/" in u_low or "مسلسل" in g_low:
                    result_data["series"].append(curr_item)
                elif "/movie/" in u_low or any(u_low.endswith(x) for x in [".mp4", ".mkv"]) or "فلم" in g_low:
                    result_data["movies"].append(curr_item)
                else:
                    result_data["live"].append(curr_item)
                curr_item = None

        return jsonify({"success": True, "data": result_data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
