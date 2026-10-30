"""Download public Bilibili music videos into the normal media workflow."""

import re
from urllib.parse import parse_qs, urlparse

import httpx

from .media import probe, run

BVID = re.compile(r"BV[0-9A-Za-z]{10}")
HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/130 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}
LIMIT = 512 * 1024 * 1024


def parse_link(value):
    value = value.strip()
    if BVID.fullmatch(value):
        return value, 1
    parsed = urlparse(value)
    if (
        parsed.scheme not in ("http", "https")
        or parsed.hostname not in ("www.bilibili.com", "bilibili.com", "m.bilibili.com")
        or parsed.port not in (None, 80, 443)
        or parsed.username
        or parsed.password
    ):
        raise ValueError("请输入 B站视频完整链接或 BV号，例如 BV18i421e7cJ")
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)
    page = parse_qs(parsed.query).get("p", ["1"])[0]
    if (
        not match
        or not page.isascii()
        or not page.isdigit()
        or not 1 <= int(page) <= 10000
    ):
        raise ValueError("B站视频链接或分P无效")
    return match[1], int(page)


def api_data(client, endpoint, **params):
    response = client.get("https://api.bilibili.com/" + endpoint, params=params)
    response.raise_for_status()
    body = response.json()
    if body.get("code") != 0 or not isinstance(body.get("data"), dict):
        raise ValueError(f"B站接口不可用：{body.get('message', '请稍后重试')}")
    return body["data"]


def download_stream(client, urls, target, limit, progress):
    for url in urls:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or not any(
                host == domain or host.endswith("." + domain)
                for domain in ("bilivideo.com", "bilivideo.cn", "hdslb.com")
            )
        ):
            continue
        try:
            with client.stream("GET", url, follow_redirects=False) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    continue
                size = int(response.headers.get("content-length") or 0)
                if size > limit:
                    raise ValueError("视频超过 512 MB 下载限制")
                total = 0
                reported = -1
                with target.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > limit:
                            raise ValueError("视频超过 512 MB 下载限制")
                        output.write(chunk)
                        bucket = total // (5 * 1024 * 1024)
                        if bucket != reported:
                            progress(f"{target.stem}：已下载 {total / 1048576:.1f} MB")
                            reported = bucket
                if not total or (size and total != size):
                    raise ValueError("下载不完整，请重试")
                return total
        except httpx.HTTPError:
            continue
    raise ValueError("B站媒体下载失败，请稍后重试或上传本地视频")


def download_video(source, folder, settings, progress):
    bvid, page = source["bvid"], source["page"]
    target = folder / "video.mp4"
    # Only the validated, atomically promoted output is reusable after interruption.
    if target.is_file() and target.stat().st_size:
        return target
    progress("正在获取 B站视频信息")
    with httpx.Client(headers=HEADERS, timeout=httpx.Timeout(35, connect=10)) as client:
        view = api_data(client, "x/web-interface/view", bvid=bvid)
        pages = view.get("pages") or []
        if page > len(pages):
            raise ValueError("该视频不存在指定分P")
        data = api_data(
            client,
            "x/player/playurl",
            bvid=bvid,
            cid=pages[page - 1]["cid"],
            qn=64,
            fnval=16,
            fourk=0,
        )
        dash = data.get("dash") or {}
        videos = sorted(
            (
                v
                for v in dash.get("video", [])
                if v.get("codecs", "").startswith("avc1") and v.get("id", 0) <= 64
            ),
            key=lambda v: v.get("bandwidth", 0),
            reverse=True,
        )
        audios = sorted(
            (
                a
                for a in dash.get("audio", [])
                if a.get("codecs", "").startswith("mp4a")
            ),
            key=lambda a: a.get("bandwidth", 0),
            reverse=True,
        )
        temporary = folder / "download.mp4"
        if videos and audios:
            video, audio = videos[0], audios[0]
            size = download_stream(
                client,
                [video["baseUrl"], *(video.get("backupUrl") or [])],
                folder / "video.m4s",
                LIMIT,
                progress,
            )
            download_stream(
                client,
                [audio["baseUrl"], *(audio.get("backupUrl") or [])],
                folder / "audio.m4s",
                LIMIT - size,
                progress,
            )
            progress("下载完成，正在合并视频与音轨")
            run(
                [
                    settings.tool("ffmpeg"),
                    "-nostdin",
                    "-y",
                    "-i",
                    folder / "video.m4s",
                    "-i",
                    folder / "audio.m4s",
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    temporary,
                ]
            )
        else:
            data = api_data(
                client,
                "x/player/playurl",
                bvid=bvid,
                cid=pages[page - 1]["cid"],
                qn=64,
                fnval=0,
            )
            parts = data.get("durl") or []
            if len(parts) != 1:
                raise ValueError("该视频暂无可下载的公开音视频，请上传本地文件")
            download_stream(
                client,
                [parts[0]["url"], *(parts[0].get("backup_url") or [])],
                temporary,
                LIMIT,
                progress,
            )
        probe(temporary, settings)
        temporary.replace(target)
    return target
