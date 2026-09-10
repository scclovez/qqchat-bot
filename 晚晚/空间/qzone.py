# -*- coding: utf-8 -*-
"""QQ 空间功能：主动发说说、自己说说评论自动回复、好友动态自动点赞/评论。

基于 SnowLuma 的 OneBot 扩展 action（需要 QQ 空间已开通、SnowLuma 正常连接）：
  send_qzone_msg（发说说）/ get_qzone_msg_list（自己的说说，含评论列表）/
  comment_qzone（评论）/ like_qzone、unlike_qzone（点赞）/ get_qzone_feeds（好友动态）

注意：
- SnowLuma 不推送空间事件，本模块用轮询实现（Bot 定时任务每 30 分钟检查一次）。
- get_qzone_msg_list 的 commentlist 字段由 SnowLuma 的 mapMsgList 扩展提供
  （原本只返回评论数，评论内容已在 SnowLuma 侧补上）。
- 已处理的评论/动态用 qzone_processed 表去重（重启不重复回复/点赞）。
- 所有写操作带随机间隔，防 QQ 风控。
"""
import asyncio
import html as html_lib
import logging
import os
import random
import re
import sqlite3
import time
from datetime import datetime
from urllib.parse import unquote

from 路径 import PROJECT_ROOT, data_path
from config import runtime
from sqlite_runtime import BOT_DB_LOCK, connect_bot_db

logger = logging.getLogger(__name__)

DB_PATH = data_path("晚晚", "数据", "bot_memory.db")

_lock = BOT_DB_LOCK
_conn = None

# 每轮处理上限（防刷屏/风控）
MAX_COMMENT_REPLIES = 3      # 每轮最多自动回复几条新评论
MAX_FEED_ACTIONS = 4         # 每轮最多点赞+评论几条好友动态
FEED_COMMENT_PROB = 0.35     # 好友动态默认评论概率（点赞总是做；GUI 可调）
POSTS_PER_DAY = 3            # 每天最多发几条说说（GUI 可调）
POST_IMAGE_PROB = 0.6        # 发说说配图概率（GUI 可调；0=纯文字）
ACTION_GAP_MIN, ACTION_GAP_MAX = 3.0, 8.0  # 操作间随机间隔（秒）
POST_HOUR_START, POST_HOUR_END = 8, 23      # 每天发说说的时间窗口
POST_STALE_GRACE = 2 * 3600                 # 计划时间点错过超过 2 小时则跳过（不补发旧帖）
FEED_COMMENT_RETRY_COOLDOWN = 6 * 3600      # 同一条动态评论失败后的重试冷却（秒）

# 只记录本次运行期间的失败动态，避免网络/接口异常时每轮轮询都重复请求。
_feed_comment_retry_after: dict[str, float] = {}


def _feed_comment_prob() -> float:
    """好友动态评论概率（GUI 可调，默认 0.35；0=只点赞不评论）。"""
    return float(getattr(runtime, "QZONE_FEED_COMMENT_PROB", FEED_COMMENT_PROB) or 0)

# 去重种类
KIND_DAILY_POST = "daily_post"            # ref = 本地时间戳（统计每天发布条数）
KIND_POST_DONE = "daily_post_done"        # ref = 已消费（已发/跳过）的计划发布时间戳
KIND_COMMENT_REPLIED = "comment_replied"  # ref = 评论 comment_id
KIND_FEED_LIKED = "feed_liked"            # ref = feed key
KIND_FEED_COMMENTED = "feed_commented"    # ref = feed key


# =============================================================================
# 去重表（复用 bot_memory.db）
# =============================================================================

def _get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect_bot_db(DB_PATH)
            # 主键必须是 (kind, ref) 复合：点赞和评论用同一个 feed key 做 ref，
            # 若 ref 单主键，feed_commented 会被同 key 的 feed_liked 顶掉（INSERT OR IGNORE
            # 静默忽略），导致评论去重失效、每次重启重复评论
            _conn.execute(
                "CREATE TABLE IF NOT EXISTS qzone_processed ("
                " kind TEXT NOT NULL, ref TEXT NOT NULL,"
                " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                " PRIMARY KEY (kind, ref))"
            )
            _migrate_old_schema(_conn)
            _conn.commit()
    return _conn


def _migrate_old_schema(conn: sqlite3.Connection):
    """把旧版单主键(ref)表迁移为复合主键(kind, ref)表；旧数据按 (kind,ref) 去重保留。

    注意：调用方（_get_conn）已持有共享的可重入锁，此处不再重复加锁。
    """
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='qzone_processed'"
        ).fetchone()
    except Exception:
        return
    if not row:
        return
    sql = (row["sql"] or "").lower()
    # 旧结构特征：ref 单主键（无复合主键声明）
    if "primary key (kind, ref)" in sql:
        return  # 已是新结构
    logger.info("检测到 qzone_processed 旧表结构，迁移为复合主键 (kind, ref)")
    conn.execute("ALTER TABLE qzone_processed RENAME TO qzone_processed_old")
    conn.execute(
        "CREATE TABLE qzone_processed ("
        " kind TEXT NOT NULL, ref TEXT NOT NULL,"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        " PRIMARY KEY (kind, ref))"
    )
    conn.execute(
        "INSERT OR IGNORE INTO qzone_processed (kind, ref, created_at) "
        "SELECT kind, ref, created_at FROM qzone_processed_old"
    )
    conn.execute("DROP TABLE qzone_processed_old")
    conn.commit()
    logger.info("qzone_processed 迁移完成")


def mark_processed(kind: str, ref: str):
    if not ref:
        return
    conn = _get_conn()
    with _lock:
        conn.execute(
            "INSERT OR IGNORE INTO qzone_processed (kind, ref) VALUES (?, ?)",
            (kind, str(ref)),
        )
        conn.commit()


def is_processed(kind: str, ref: str) -> bool:
    if not ref:
        return True
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            "SELECT 1 FROM qzone_processed WHERE kind = ? AND ref = ?",
            (kind, str(ref)),
        ).fetchone()
    return row is not None


# =============================================================================
# 工具
# =============================================================================

def _html_to_text(html: str) -> str:
    """把动态 HTML 粗略转成纯文本（去标签、去空白）。"""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


def _decode_feed_html(raw: str) -> str:
    """还原 feeds3 返回的 HTML，兼容实体与残留的十六进制转义。"""
    text = html_lib.unescape(str(raw or ""))
    return (text.replace(r"\x22", '"').replace(r"\x27", "'")
            .replace(r"\x3C", "<").replace(r"\x3E", ">"))


def _feed_tag_attr(tag: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(['\"])(.*?)\1", tag or "", re.I)
    return match.group(2).strip() if match else ""


def _looks_like_qzone_tid(value: str) -> bool:
    """过滤好友流里并非说说 ID 的短句柄。"""
    value = str(value or "").strip()
    return bool(re.fullmatch(r"[0-9a-fA-F]{16,}", value)
                or re.fullmatch(r"d\d+_\d+_[^\s&]+", value))


def _extract_feed_identity(feed: dict) -> tuple[str, str]:
    """从 feeds3 HTML 提取 (真实说说 tid, 动态主人 uin)。

    feeds3 顶层的 ``tid``/``uin`` 在不同模板中可能为空或含义不同；
    ``<i name="feed_data" data-tid=... data-uin=...>`` 才是评论接口需要的组合。
    """
    decoded = _decode_feed_html(feed.get("html") or "")
    feed_tag = ""
    for match in re.finditer(r"<i\b[^>]*>", decoded, re.I):
        tag = match.group(0)
        if _feed_tag_attr(tag, "name").lower() == "feed_data":
            feed_tag = tag
            break

    tid = _feed_tag_attr(feed_tag, "data-tid")
    owner = _feed_tag_attr(feed_tag, "data-uin")

    if not tid:
        outer = re.search(r"<div\b[^>]*\bdata-key\s*=\s*(['\"])(.*?)\1", decoded, re.I)
        if outer:
            tid = outer.group(2).strip()
    if not tid:
        param = re.search(r"(?:t1_tid|t1%5[Ff]tid)=([^&\"'<>\s]+)", decoded, re.I)
        if param:
            tid = unquote(param.group(1)).strip()
    if not owner:
        outer_id = re.search(r"\bid\s*=\s*(['\"])feed_(\d+)_\d+_", decoded, re.I)
        if outer_id:
            owner = outer_id.group(2)

    # 兼容已经修复的 SnowLuma；旧版则从标准说说的 key 回退。
    mapped_tid = str(feed.get("tid") or "").strip()
    key = str(feed.get("key") or "").strip()
    if not _looks_like_qzone_tid(tid):
        tid = mapped_tid if _looks_like_qzone_tid(mapped_tid) else key
    if not _looks_like_qzone_tid(tid):
        tid = ""
    if not owner:
        owner = str(feed.get("uin") or "").strip()
    return tid, owner


def _gap():
    return random.uniform(ACTION_GAP_MIN, ACTION_GAP_MAX)


def _can_retry_feed_comment(feed_key: str) -> bool:
    """同一条好友动态评论失败后，冷却期内不再请求 QQ 空间。"""
    return time.monotonic() >= _feed_comment_retry_after.get(feed_key, 0.0)


def _record_feed_comment_failure(feed_key: str):
    if feed_key:
        _feed_comment_retry_after[feed_key] = (
            time.monotonic() + FEED_COMMENT_RETRY_COOLDOWN
        )


def _clear_feed_comment_failure(feed_key: str):
    _feed_comment_retry_after.pop(feed_key, None)


def _extract_feed_image(html: str) -> str:
    """从动态 html 里提取第一张**动态配图** URL；没有返回空串。

    过滤 QQ 头像图床（qlogo 域名，html 里第一个 img 通常是发布者头像）；
    只认真正的图片图床（qpic 等）。支持 // 协议相对 URL。
    """
    if not html:
        return ""
    for m in re.finditer(r'<img[^>]+src="([^"]+)"', html):
        url = m.group(1).strip()
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith(("http://", "https://")):
            continue
        # 头像图床跳过（qlogo*.store.qq.com 是 QQ 头像，不是动态配图）
        host = url.split("://", 1)[1].split("/", 1)[0].lower()
        if host.startswith("qlogo"):
            continue
        return url
    return ""


async def _vision_describe(llm_chat, image_path_or_url: str, prompt_text: str) -> str:
    """读取/下载图片 → 调视觉模型识别 → 返回文字描述；失败返回空串。

    llm_chat: async (messages, **kw) -> str（DeepSeek chat，可传 model 参数）
    """
    try:
        import base64
        if image_path_or_url.startswith(("http://", "https://")):
            # 走安全下载：重定向逐跳校验主机（防 SSRF）+ 25MB 大小上限（防内存峰值）
            from live_info import download_bytes_safe
            raw, mime = await download_bytes_safe(image_path_or_url, max_bytes=25 * 1024 * 1024, timeout=20)
            if raw is None:
                logger.warning("视觉识别图片下载失败/被拦截: %s", image_path_or_url[:120])
                return ""
        else:
            if not os.path.isfile(image_path_or_url):
                return ""
            with open(image_path_or_url, "rb") as f:
                raw = f.read()
            ext = os.path.splitext(image_path_or_url)[1].lower()
            mime = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "image/jpeg")
        b64 = base64.b64encode(raw).decode("ascii")
        from config import config
        resp = await llm_chat(
            [{"role": "user", "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}],
            model=config.DEEPSEEK_VISION_MODEL, max_tokens=200, disable_thinking=True,
        )
        return (resp or "").strip()
    except Exception as e:
        logger.warning("视觉识别异常: %s", e)
        return ""


# =============================================================================
# 1) 主动发说说（每天最多 POSTS_PER_DAY 条，条间随机区间冷却）
# =============================================================================

def _today_post_count() -> int:
    """今天（本地日期）已发说说条数。ref 存本地时间戳，前缀即本地日期。"""
    prefix = datetime.now().strftime("%Y%m%d")
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM qzone_processed "
            "WHERE kind = ? AND ref LIKE ?",
            (KIND_DAILY_POST, prefix + "%"),
        ).fetchone()
    return row["cnt"] if row else 0


def _post_interval_range() -> tuple:
    """说说条间间隔随机区间（小时，GUI 可调；区间无效时返回 (0, 0) = 关闭冷却）。"""
    lo = float(getattr(runtime, "QZONE_POST_INTERVAL_MIN", 0) or 0)
    hi = float(getattr(runtime, "QZONE_POST_INTERVAL_MAX", 0) or 0)
    if lo < 0:
        lo = 0
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _daily_post_times() -> list:
    """今天计划发说说的时刻（本地 unix 秒，升序）。按日期种子**确定性**生成：
    同一天内稳定（重启也不重排、不连发）；时间散落在 8–23 点窗口内、彼此错开
    （早/午/晚各有一次），每次间隔 >= QZONE_POST_INTERVAL_MIN 小时（0=不限制）。
    """
    n = _posts_per_day()
    day0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = day0.replace(hour=POST_HOUR_START)
    end = day0.replace(hour=POST_HOUR_END)
    total = max(1.0, (end - start).total_seconds())
    min_gap = _post_interval_range()[0] * 3600  # MIN 小时 → 秒（0=不限制）
    seg = total / max(1, n)
    rnd = random.Random(day0.strftime("%Y%m%d") + "_qzone")
    start_ts = start.timestamp()
    times = []
    for i in range(n):
        offset = seg * i + rnd.uniform(0, seg * 0.5)
        times.append(int(start_ts + offset))
    # 保证最小间隔（极端配置下也不挤在一起）
    if min_gap > 0:
        for i in range(1, len(times)):
            if times[i] - times[i - 1] < min_gap:
                times[i] = times[i - 1] + int(min_gap)
    # 钳制，不越过窗口终点
    end_ts = int(end.timestamp())
    return sorted(min(t, end_ts) for t in times)


def _consumed_slots() -> set:
    """已消费（已发/跳过）的计划发布时间戳集合。"""
    conn = _get_conn()
    with _lock:
        rows = conn.execute(
            "SELECT ref FROM qzone_processed WHERE kind = ?", (KIND_POST_DONE,)
        ).fetchall()
    out = set()
    for r in rows:
        try:
            out.add(int(r["ref"]))
        except (TypeError, ValueError):
            pass
    return out


def _next_pending_slot() -> float:
    """下一个应发布的槽（unix 秒）；未到计划时间返回 0。

    已到点但**严重过期**（错过 > POST_STALE_GRACE）的槽直接消费跳过，
    避免机器人重启后"补发/连发"旧帖——真人错过那个点就是错过了。
    """
    now = time.time()
    consumed = _consumed_slots()
    for t in _daily_post_times():
        if t in consumed:
            continue
        if t <= now:
            if now - t <= POST_STALE_GRACE:
                return float(t)
            mark_processed(KIND_POST_DONE, str(t))
            consumed.add(t)
        else:
            break
    return 0.0


def _posts_per_day() -> int:
    return max(1, int(getattr(runtime, "QZONE_POSTS_PER_DAY", POSTS_PER_DAY) or POSTS_PER_DAY))


def _post_image_prob() -> float:
    """发说说配图概率（GUI 可调，默认 0.6；0=纯文字说说）。"""
    return float(getattr(runtime, "QZONE_POST_IMAGE_PROB", POST_IMAGE_PROB) or 0)


def _image_check_enabled() -> bool:
    """说说配图质量检查开关（视觉模型确认清晰/有人物，不合格重新生成）。"""
    return int(getattr(runtime, "QZONE_IMAGE_CHECK", 1) or 0) != 0


def _to_qzone_image_src(path_or_url: str) -> str:
    """把本地路径/URL 转成 send_qzone_msg images 接受的源格式（file:// http:// base64://）。"""
    s = (path_or_url or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://", "file://", "base64://")):
        return s
    return "file:///" + s.replace("\\", "/")


async def send_qzone_update(api_call, llm_chat, image_gen_cb=None, mood_user_id=""):
    """生成并发送一条空间说说；达每日上限/未到计划发布时间/窗口外返回空串。

    成功发布返回说说内容（供"发完喊你看"等联动）；失败/跳过返回空串。

    配图：image_gen_cb 非空且按概率命中时，生成一张图随说说发布
    （image_gen_cb: async () -> 本地路径或 URL；后端复用现有本地/云端设置）。

    api_call: async (action, params) -> dict（OneBot 响应）
    llm_chat: async (messages, **kw) -> str（DeepSeek chat）
    """
    count = _today_post_count()
    limit = _posts_per_day()
    if count >= limit:
        logger.info("空间说说: 今天已发 %d/%d 条，跳过", count, limit)
        return ""
    # 空间接口失败退避：共用 30 分钟冷却（防限流时每 5 分钟硬撞）
    if _qzone_in_fail_cooldown():
        return ""
    # 计划发布时间：到点才发（每天预排、错开；错过 >2 小时跳过，不连发）
    target = _next_pending_slot()
    if not target:
        logger.info("空间说说: 未到计划发布时间，跳过")
        return ""
    hour = datetime.now().hour
    if not (POST_HOUR_START <= hour < POST_HOUR_END):
        logger.info("空间说说: 非发说说时段（%02d-%02d点），跳过", POST_HOUR_START, POST_HOUR_END)
        return ""
    try:
        from personality import build_system_prompt
        import live_info
        prompt = (
            f"现在时间是{live_info.now_text()}。请以你的身份发一条 QQ 空间说说。\n"
            "要像真人发空间那样，分享**你自己此刻的日常**：在做什么、心情、吐槽、"
            "路上看到的小事、刚吃到的好东西等，语气符合你的人设（嘴贫、温柔、生活化）。\n"
            "**重要：空间是给好友看的公开动态，不是和男朋友的私聊**——"
            "绝对不要提男朋友/对象/老公/他，不要写\"想你\"\"等你\"\"来陪我\"这类对恋人"
            "说话的内容，不要撒娇式呼叫任何人，就像普通女生随手分享日常一样。\n"
            f"可以结合此刻你可能在做的事（{live_info.current_activity()}），"
            "写一句到两句话，50字以内，不要括号动作描写，不要@任何人，直接输出说说内容。"
        )
        # 多模态联动：空间文案与持续情绪一致，负面强时不突然配欢快图片。
        allow_mood_image = True
        try:
            import liveness
            mood = liveness.emotion_snapshot(mood_user_id) if mood_user_id else {}
            negative = max(mood.get("angry", 0), mood.get("hurt", 0))
            if negative >= 18 or mood.get("tired", 0) >= 28:
                prompt += ("\n（你此刻还有低落/别扭/疲惫的余波：文案要安静克制，"
                           "不要突然写得特别欢快，也不要强装元气。）")
            allow_mood_image = (not mood_user_id
                                or liveness.emotion_allows_playful_media(mood_user_id))
        except Exception:
            pass
        content = await llm_chat(
            [{"role": "system", "content": build_system_prompt()}, {"role": "user", "content": prompt}],
            max_tokens=120, disable_thinking=True,
        )
        content = (content or "").strip()
        if len(content) < 3:
            logger.warning("说说内容生成无效，跳过: %r", content[:50])
            return ""
        # 配图：按概率用 image_gen_cb 生成一张图随说说发布；
        # 开启质量检查时用视觉模型确认清晰/有人物，不合格重新生成（最多 3 次）
        params = {"content": content}
        if (image_gen_cb and allow_mood_image and _post_image_prob() > 0
                and random.random() < _post_image_prob()):
            img = await image_gen_cb()
            if img and _image_check_enabled():
                for attempt in range(3):
                    check = await _vision_describe(
                        llm_chat, img,
                        "检查这张图片：是否清晰、画面是否正常、有没有明显生成缺陷"
                        "（畸变/模糊/奇怪的形状/画面破损）？正常只回复OK，有问题用一句话说明问题。",
                    )
                    if check and "ok" in check.lower() and len(check) < 12:
                        break
                    logger.warning("说说配图检查不过关（%s），重新生成（第 %d 次）",
                                   (check or "")[:40], attempt + 1)
                    img = await image_gen_cb()
                    if not img:
                        break
                else:
                    img = ""  # 3 次都不合格 → 放弃配图
            src = _to_qzone_image_src(img)
            if src:
                params["images"] = [src]
                logger.info("说说配图: %s", src[:80])
            else:
                logger.warning("说说配图生成失败/检查未通过，改发纯文字说说")
        resp = await api_call("send_qzone_msg", params)
        if resp is None or resp.get("status") == "failed":
            _qzone_mark_failure()
            return ""
        mark_processed(KIND_DAILY_POST, datetime.now().strftime("%Y%m%d%H%M%S"))
        # 记录该计划槽已消费，同一天不会在同一个时间点重复发
        mark_processed(KIND_POST_DONE, str(int(target)))
        logger.info("已发布空间说说（今日 %d/%d%s）: %s",
                    count + 1, limit, "，带图" if params.get("images") else "", content[:50])
        return content
    except Exception as e:
        logger.warning("发说说失败: %s", e)
        return ""


def _relation_desc(uin, boyfriend_uin="") -> str:
    """判断评论者/动态主人与自己的关系（男友 or 普通好友）。

    boyfriend_uin 通常来自 PROACTIVE_ONLY_USER_ID（用户设置只对这位发主动消息）。
    """
    if boyfriend_uin and str(uin or "") == str(boyfriend_uin):
        return "你的男朋友"
    return "你的QQ好友"


# =============================================================================
# 2) 自己说说的新评论 → 自动回复
# =============================================================================

# 空间接口失败退避：QQ 空间接口容易被限流（legacy route rate-limited，
# 降级路由还会返回 msglist=null）。失败后进入冷却，不再每 5 分钟硬撞，等限流恢复。
_qzone_fail_until = 0.0
QZONE_FAIL_COOLDOWN_SECONDS = 30 * 60


def _qzone_in_fail_cooldown() -> bool:
    return time.time() < _qzone_fail_until


def _qzone_mark_failure():
    global _qzone_fail_until
    _qzone_fail_until = time.time() + QZONE_FAIL_COOLDOWN_SECONDS
    logger.warning("空间接口失败，进入 %d 分钟退避", QZONE_FAIL_COOLDOWN_SECONDS // 60)


async def reply_new_comments(api_call, llm_chat, self_uin, boyfriend_uin=""):
    """轮询自己说说的新评论并逐条自动回复（按 tid+comment_id 去重，跨说说不冲突）。

    评论者是男友（boyfriend_uin）时按恋人关系回复，否则按好友关系回复，
    避免把"好友"误当成"男友"撒娇、或把男友当路人。
    空间接口失败会进入 30 分钟退避（防限流恶性循环）。
    返回本轮回复数。
    """
    if _qzone_in_fail_cooldown():
        return 0
    try:
        resp = await api_call("get_qzone_msg_list", {"pos": 0, "num": 20})
        if resp is None:
            _qzone_mark_failure()
            return 0
        data = resp.get("data") or {}
        # 失败（retcode!=0）或响应结构异常（msglist 不是数组）→ 退避
        if resp.get("status") == "failed" or not isinstance(data.get("msglist"), list):
            _qzone_mark_failure()
            return 0
        msglist = data.get("msglist") or []
    except Exception as e:
        logger.warning("拉取说说列表失败: %s", e)
        _qzone_mark_failure()
        return 0

    replied = 0
    for msg in msglist:
        tid = msg.get("tid", "")
        for c in (msg.get("commentlist") or []):
            content = (c.get("content") or "").strip()
            uin = str(c.get("uin") or "")
            cid = str(c.get("comment_id") or (tid + uin))
            if not content or not tid or not cid:
                continue
            if uin == str(self_uin or ""):
                continue  # 自己的评论不回复
            # 去重 key 必须带上说说 tid：QQ 空间每条说说的评论 id 独立计数，
            # 裸 comment_id 会跨说说重复（如多条说说都有 id=1）导致漏回复
            dedup_key = f"{tid}:{cid}"
            if is_processed(KIND_COMMENT_REPLIED, dedup_key):
                continue
            try:
                from personality import build_system_prompt
                relation = _relation_desc(uin, boyfriend_uin)
                reply = await llm_chat(
                    [{"role": "system", "content": build_system_prompt()},
                     {"role": "user", "content": (
                         f"你的 QQ 空间说说下面，{relation}评论了：\n「{content}」\n"
                         "请以本人身份回复这条评论：短（一两句话）、符合你的说话风格"
                         "（嘴贫、温柔、粘人），不要括号动作描写，直接输出回复。"
                     )}],
                    max_tokens=100, disable_thinking=True,
                )
                reply = (reply or "").strip()
                if not reply:
                    continue
                # 评论自己的说说：显式传 target_uin=自己（说说主人），避免 CGI 缺参
                resp = await api_call("comment_qzone", {"tid": tid, "content": reply,
                                                        "target_uin": str(self_uin or "")})
                # 只在真正成功后标记已处理：失败不 mark，下轮重试（防永久漏回复）
                if resp is None or resp.get("status") == "failed" \
                        or resp.get("retcode") not in (None, 0):
                    logger.warning("回复空间评论失败（不标记已处理，稍后重试）: %s", tid)
                    continue
                mark_processed(KIND_COMMENT_REPLIED, dedup_key)
                replied += 1
                logger.info("已回复空间评论 [%s]: %s -> %s", uin, content[:20], reply[:30])
                if replied >= MAX_COMMENT_REPLIES:
                    return replied
                await asyncio.sleep(_gap())
            except Exception as e:
                logger.warning("回复空间评论失败: %s", e)
    if replied:
        logger.info("空间评论检查完成: 回复 %d 条新评论", replied)
    else:
        logger.debug("空间评论检查完成: 无新评论")
    return replied


# =============================================================================
# 3) 好友空间动态 → 自动点赞 + 按概率评论
# =============================================================================

async def like_and_comment_feeds(api_call, llm_chat, self_uin, boyfriend_uin=""):
    """拉好友动态流：对未处理的新动态点赞，并按概率生成评论。

    动态主人是男友（boyfriend_uin）时按恋人关系评论，否则按好友关系评论。
    返回 (点赞数, 评论数)。
    """
    try:
        resp = await api_call("get_qzone_feeds", {"page_num": 1, "count": 10})
        if resp is None:
            _qzone_mark_failure()
            return 0, 0
        if resp.get("status") == "failed" or not isinstance((resp.get("data") or {}).get("feeds"), list):
            _qzone_mark_failure()
            return 0, 0
        feeds = (resp.get("data") or {}).get("feeds") or []
    except Exception as e:
        logger.warning("拉取好友动态失败: %s", e)
        _qzone_mark_failure()
        return 0, 0

    liked = commented = 0
    for feed in feeds:
        tid, uin = _extract_feed_identity(feed)
        key = str(feed.get("key") or tid or "")
        if not key or not uin or uin == str(self_uin or ""):
            continue
        if "advertisement" in key or int(feed.get("appid") or 0) == 6600:
            continue  # 广告动态
        # 好友动态流的 key 不能替代说说 tid。缺 tid 时不提交无效请求，
        # 并在冷却后才再次记录，避免每轮轮询重复刷警告。
        if not tid:
            if _can_retry_feed_comment(key):
                logger.warning("好友动态缺少 tid，跳过点赞/评论: %s", key)
                _record_feed_comment_failure(key)
            continue
        try:
            # 点赞（总是做，去重）
            if not is_processed(KIND_FEED_LIKED, key):
                # 点赞/评论 CGI 需要说说 tid；key 只是好友动态流的去重标识。
                resp = await api_call("like_qzone", {
                    "tid": tid,
                    "target_uin": uin,
                    "abstime": int(feed.get("time") or 0),
                })
                # 失败不 mark：下轮重试（防失败被记"已点赞"导致永久漏赞）
                if resp is None or resp.get("status") == "failed" \
                        or resp.get("retcode") not in (None, 0):
                    logger.warning("点赞动态失败（不标记已处理，稍后重试）: %s", key)
                else:
                    mark_processed(KIND_FEED_LIKED, key)
                    liked += 1
                    await asyncio.sleep(_gap())
            # 评论（按概率，去重）
            if (not is_processed(KIND_FEED_COMMENTED, key)
                    and _can_retry_feed_comment(key)
                    and random.random() < _feed_comment_prob()):
                text = _html_to_text(feed.get("html") or "")
                if text:
                    # 动态带图时：先视觉识别图片内容，评论更贴切（识别失败不影响文字评论）
                    img_desc = ""
                    img_url = _extract_feed_image(feed.get("html") or "")
                    if img_url:
                        img_desc = await _vision_describe(
                            llm_chat, img_url,
                            "用一句话描述这张图片的内容（主体、场景），30字以内。",
                        )
                        if img_desc:
                            logger.info("好友动态图片识别: %s", img_desc[:50])
                    from personality import build_system_prompt
                    relation = _relation_desc(uin, boyfriend_uin)
                    user_prompt = f"这是{relation}的一条空间动态：\n「{text[:200]}」\n"
                    if img_desc:
                        user_prompt += f"（动态图片内容：{img_desc}）\n"
                    user_prompt += (
                        f"请以你（bot）自己的身份，以「{relation}」的身份关系写一句评论，"
                        "语气符合你的人设（嘴贫、温柔、粘人），几个字到一两句话，"
                        "不要括号动作描写，不要@，直接输出评论。"
                    )
                    comment = await llm_chat(
                        [{"role": "system", "content": build_system_prompt()},
                         {"role": "user", "content": user_prompt}],
                        max_tokens=80, disable_thinking=True,
                    )
                    comment = (comment or "").strip()
                    if comment:
                        # 评论 CGI 的 topicId 由 owner + 原说说 tid 组成，不能传好友流 key。
                        resp = await api_call("comment_qzone", {"tid": tid, "content": comment,
                                                               "target_uin": uin})
                        # 失败不 mark：下轮重试（防失败被记"已评论"导致永久漏评）
                        if resp is None or resp.get("status") == "failed" \
                                or resp.get("retcode") not in (None, 0):
                            _record_feed_comment_failure(key)
                            logger.warning("评论动态失败，已冷却 %.0f 小时后再试: %s",
                                           FEED_COMMENT_RETRY_COOLDOWN / 3600, key)
                            continue
                        mark_processed(KIND_FEED_COMMENTED, key)
                        _clear_feed_comment_failure(key)
                        commented += 1
                        logger.info("已评论好友动态 [%s]: %s", uin, comment[:30])
                        await asyncio.sleep(_gap())
        except Exception as e:
            logger.warning("好友动态操作失败: %s", e)
        if liked + commented >= MAX_FEED_ACTIONS:
            break
    logger.info("好友动态检查完成: 点赞 %d 条, 评论 %d 条（本轮）", liked, commented)
    return liked, commented
