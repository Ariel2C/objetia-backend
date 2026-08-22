import urllib.request
import json

def test(name, url, method="GET", body=None):
    try:
        data = json.dumps(body).encode("utf-8") if body else None
        headers = {"Content-Type": "application/json"} if body else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        resp = urllib.request.urlopen(req)
        print(f"[OK] {name}: HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        print(f"[HTTP {e.code}] {name}: {e.read().decode('utf-8')}")
    except Exception as ex:
        print(f"[ERROR] {name}: {ex}")

if __name__ == "__main__":
    test("Health Check", "http://localhost:8000/health")
    test("Featured Products", "http://localhost:8000/products/featured")
    test("CMS Sections", "http://localhost:8000/cms/sections/")
    test("CMS Layout", "http://localhost:8000/cms/layout/")
