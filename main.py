import os
import re
from urllib.parse import urlparse, parse_qs
import requests
import urllib3
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

@app.route('/')
def home():
    return jsonify({"service": "Albasem-TV Engine", "status": "running"})

@app.route('/stream.m3u')
def stream_m3u():
    url = request.args.get('url', '')
    name = request.args.get('name', 'Live Stream')
    if not url:
        return "رابط غير موجود", 400

    content = f"#EXTM3U\n#EXTINF:-1,{name}\n{url}\n"
    return Response(
        content,
        mimetype="audio/x-mpegurl",
        headers={"Content-Disposition": f'inline; filename="stream.m3u"'}
    )

def fetch_category_map(api_url, user, pwd, action, headers):
    cat_map = {}
    try:
        r = requests.get(api_url, params={"username": user, "password": pwd, "action": action}, headers=headers, timeout=10, verify=False)
        if r.status_code == 200 and isinstance(r.json(), list):
            for c in r.json():
                c_id = str(c.get("category_id", ""))
                c_name = c.get("category_name", "").strip()
                if c_id and c_name:
                    cat_map[c_id] = c_name
    except Exception:
        pass
    return cat_map

@app.route('/api/parse', methods=['POST'])
def parse_iptv():
    payload = request.get_json(silent=True) or {}
    url = payload.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "message": "الرابط فارغ"}), 400

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
                    grp_name = live_cats.get(cat_id, "عام")
                    result_data["live"].append({
                        "name": item.get("name", "قناة"),
                        "logo": item.get("stream_icon", ""),
                        "group": grp_name,
                        "url": f"{base_url}/live/{user}/{pwd}/{s_id}.ts"
                    })

            r_vod = requests.get(api_url, params={"username": user, "password": pwd, "action": "get_vod_streams"}, headers=headers, timeout=15, verify=False)
            if r_vod.status_code == 200 and isinstance(r_vod.json(), list):
                for item in r_vod.json():
                    s_id = item.get("stream_id")
                    cat_id = str(item.get("category_id", ""))
                    grp_name = vod_cats.get(cat_id, "أفلام")
                    ext = item.get("container_extension", "mp4")
                    result_data["movies"].append({
                        "name": item.get("name", "فيلم"),
                        "logo": item.get("stream_icon", ""),
                        "group": grp_name,
                        "url": f"{base_url}/movie/{user}/{pwd}/{s_id}.{ext}"
                    })

            r_series = requests.get(api_url, params={"username": user, "password": pwd, "action": "get_series"}, headers=headers, timeout=15, verify=False)
            if r_series.status_code == 200 and isinstance(r_series.json(), list):
                for item in r_series.json():
                    s_id = item.get("series_id")
                    cat_id = str(item.get("category_id", ""))
                    grp_name = series_cats.get(cat_id, "مسلسلات")
                    result_data["series"].append({
                        "name": item.get("name", "مسلسل"),
                        "logo": item.get("cover", ""),
                        "group": grp_name,
                        "url": f"{base_url}/series/{user}/{pwd}/{s_id}"
                    })

            return jsonify({"success": True, "data": result_data})
        except Exception:
            pass

    try:
        r = requests.get(url, headers=headers, timeout=35, verify=False)
        r.encoding = 'utf-8'
        lines = r.text.splitlines()
        curr_item = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
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
