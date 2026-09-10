# -*- coding: utf-8 -*-
"""实时信息：当前时间、天气（Open-Meteo 免费接口）、联网搜索（Bing）。

全部可选、带超时、失败静默返回空 —— 不影响正常回复流程。
"""
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# WMO 天气代码 → 中文描述
WMO_CODES = {
    0: "晴朗", 1: "基本晴朗", 2: "多云", 3: "阴天",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def now_text() -> str:
    """当前时间文本：2026年8月22日 星期六 13:15:30"""
    now = datetime.now()
    return f"{now.year}年{now.month}月{now.day}日 {WEEKDAYS[now.weekday()]} {now.strftime('%H:%M:%S')}"


# 时间相关触发词：用户消息提到这些词时，注入"此刻在做什么"的动态人设描述
TIME_TRIGGERS = (
    "现在", "几点", "时间", "几点了", "几点钟", "之前", "刚才", "刚刚",
    "今天", "昨天", "明天", "后天", "早上", "中午", "下午", "晚上", "凌晨",
    "在干嘛", "在干什么", "干嘛呢", "做什么", "干哈", "睡了没", "睡了么",
    "起床", "睡觉", "吃饭", "上课", "下课",
)


_activity_cache = {"hour": -1, "text": ""}


def current_activity() -> str:
    """返回连续生活状态；数据库不可用时才回退到旧的时段活动。

    连续状态包含开始与结束时间，同一活动不会因再次调用而随机变化。
    """
    try:
        import life_state
        return life_state.current_activity_text()
    except Exception as e:
        logger.debug("连续生活状态读取失败，回退时段活动: %s", e)
    global _activity_cache
    hour = datetime.now().hour
    if _activity_cache["hour"] == hour:
        return _activity_cache["text"]
    text = _pick_activity(hour)
    _activity_cache = {"hour": hour, "text": text}
    return text


def daily_scene() -> str:
    """主动消息使用同一条生活线，不再另造一套宽泛时段场景。"""
    return current_activity()


def current_activity_for(user_id: str = "") -> str:
    """角色只有一条全局生活线，不再按联系人或最近几条回复各猜一套状态。"""
    return current_activity()


def _pick_activity(hour: int) -> str:
    """按小时从多套说法里随机挑一个（画室只占其中一小部分）。"""
    import random
    if hour < 5:
        return random.choice([
            "深夜了，你早该睡却还在刷手机，困得直打哈欠",
            "凌晨了，你窝在被子里刷视频，眼皮开始打架",
            "夜深人静，你在听歌发呆，还不想睡",
        ])
    if hour < 7:
        return random.choice([
            "刚醒，正赖在床上不想起来",
            "被闹钟吵醒，闭着眼摸手机",
        ])
    if hour < 8:
        return random.choice([
            "刚爬起来洗漱，迷迷糊糊准备出门",
            "在镜子前发呆，想着今天穿什么",
        ])
    if hour < 12:
        return random.choice([
            "在上专业课，偷偷回你消息",
            "上午有课，趁课间回你一句",
            "在图书馆翻资料，手机震动了一下",
            "在画室上专业课，手上还沾着铅笔灰",
        ])
    if hour < 14:
        return random.choice([
            "中午刚打完饭，正一边吃一边回你消息",
            "午休时间，趴在桌上眯了一会儿",
            "吃完饭在校园里慢悠悠地散步",
        ])
    if hour < 17:
        return random.choice([
            "下午没课，窝在宿舍摸鱼",
            "下午在图书馆自习，时不时看一眼手机",
            "下午在画室赶作业，偷懒回你两句",
            "下午出来逛了逛，顺路买了杯奶茶",
        ])
    if hour < 19:
        return random.choice([
            "傍晚刚下课，路上慢悠悠地走",
            "傍晚回宿舍，顺路买了点吃的",
            "傍晚在操场走了两圈",
        ])
    if hour < 23:
        return random.choice([
            "在宿舍打游戏/看番，手机一响就秒回你",
            "窝在床上刷手机，百无聊赖",
            "刚洗完澡，头发还湿着",
        ])
    return random.choice([
        "夜深了，你还在强撑没睡，一边打哈欠一边回你消息",
        "快零点了，你缩在被子里不肯睡",
    ])


async def fetch_weather(city: str, timeout: float = 8.0) -> str:
    """查询城市当前天气（Open-Meteo，免费无 Key）。失败返回空串。"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "zh", "format": "json"},
            )
            geo.raise_for_status()
            results = (geo.json() or {}).get("results") or []
            if not results:
                logger.warning("天气：找不到城市 %s", city)
                return ""
            lat, lon = results[0]["latitude"], results[0]["longitude"]
            place = results[0].get("name", city)
            admin = results[0].get("admin1", "")

            w = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,"
                                "weather_code,wind_speed_10m"),
                    "timezone": "auto", "forecast_days": 1,
                },
            )
            w.raise_for_status()
            cur = w.json()["current"]
            desc = WMO_CODES.get(cur["weather_code"], "未知")
            return (f"{place}{admin} 当前天气：{desc}，气温 {cur['temperature_2m']}°C，"
                    f"体感 {cur['apparent_temperature']}°C，湿度 {cur['relative_humidity_2m']}%，"
                    f"风速 {cur['wind_speed_10m']}km/h")
    except Exception as e:
        logger.warning("获取天气失败: %s", e)
        return ""


async def web_search(query: str, timeout: float = 8.0) -> str:
    """联网搜索（Bing 中文）：返回前 4 条标题/摘要/链接。失败返回空串。"""
    try:
        import html
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     headers={"User-Agent": _UA}) as client:
            resp = await client.get(
                "https://cn.bing.com/search",
                params={"q": query, "setlang": "zh-hans"},
            )
            resp.raise_for_status()
            text = resp.text
        blocks = re.findall(r'<li class="b_algo".*?</li>', text, re.S)[:4]
        if not blocks:
            logger.warning("搜索：Bing 未返回结果块")
            return ""
        out = []
        for b in blocks:
            t = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
            if not t:
                continue
            url = t.group(1)
            title = html.unescape(re.sub(r"<[^>]+>", "", t.group(2))).strip()
            sn = re.search(r"<p[^>]*>(.*?)</p>", b, re.S)
            snippet = html.unescape(re.sub(r"<[^>]+>", "", sn.group(1))).strip() if sn else ""
            out.append(f"- {title}\n  {snippet}\n  {url}")
        return "\n".join(out)
    except Exception as e:
        logger.warning("联网搜索失败: %s", e)
        return ""


# =============================================================================
# 安全下载：逐跳校验主机，防 SSRF（防止重定向跳进内网/回环地址）
# =============================================================================

def _host_is_private(url: str) -> bool:
    """URL 主机是否为内网/回环地址；域名返回 False（不拦截，QQ 图片 CDN 都是域名）。

    与 qq_bot._is_private_host 同策略，独立实现以免循环导入。
    """
    try:
        import ipaddress
        from urllib.parse import urlparse
        host = (urlparse(url).netloc or "").split("@")[-1]
        # 剥掉端口（IPv6 中括号形式如 [::1]:8080 单独处理）
        if host.startswith("["):
            host = host.split("]")[0].lstrip("[")
        else:
            host = host.split(":", 1)[0]
        ip = ipaddress.ip_address(host)
        return (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
    except ValueError:
        return False  # 域名：不拦截
    except Exception:
        return True


async def download_bytes_safe(url: str, max_bytes: int = 25 * 1024 * 1024,
                              timeout: float = 20.0) -> tuple:
    """下载 URL 二进制内容，重定向**逐跳**校验目标主机（防 SSRF）。

    - 手动处理重定向（最多 5 跳），每跳都校验主机不是内网/回环
    - 超过 max_bytes 中止（防内存峰值/恶意大文件）
    返回 (bytes, mime)；失败/被拦截返回 (None, None)。
    """
    if not url or not url.startswith(("http://", "https://")):
        return None, None
    import httpx
    from urllib.parse import urljoin
    cur = url
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False,
                                     headers={"User-Agent": _UA}) as client:
            for _hop in range(6):  # 原地址 + 最多 5 次重定向
                if _host_is_private(cur):
                    logger.warning("拒绝下载内网/回环地址: %s", cur)
                    return None, None
                async with client.stream("GET", cur) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            return None, None
                        cur = urljoin(cur, loc)  # 下一跳继续校验
                        continue
                    if resp.status_code != 200:
                        logger.warning("下载 URL HTTP %s: %s", resp.status_code, cur)
                        return None, None
                    content = b""
                    async for chunk in resp.aiter_bytes():
                        content += chunk
                        if len(content) > max_bytes:
                            logger.warning("下载 URL 过大，中止: %s", cur)
                            return None, None
                    mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                    return content, mime if content else (None, None)
    except Exception as e:
        logger.warning("下载 URL 失败: %s", e)
    return None, None
