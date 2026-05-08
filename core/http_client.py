import json
from urllib import request


def request_json(method, url, api_key):
    req = request.Request(
        url,
        method=method,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url, api_key, body, timeout=240):
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
