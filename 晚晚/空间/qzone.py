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
import logging
import os
import random
import re
import sqlite3
import threading
import time
from datetime import datetime

from 路径 import PROJECT_ROOT, data_path
from config import runtime

logger = logging.getLogger(__name__)

DB_PATH = data_path("晚晚", "数据", "bot_memory.db")

_lock = threading.Lock()
_conn = None

# 每轮处理上限（防刷屏/风控）
MAX_COMMENT_REPLIES = 3      # 每轮最多自动回复几条新评论
MAX_FEED_ACTIONS = 4         # 每轮最多点赞+评论几条好友动态
FEED_COMMENT_PROB = 0.35     # 好友动态默认评论概率（点赞总是做；GUI 可调）
POSTS_PER_DAY = 3            # 每天最多发几条说说（GUI 可调）
POST_IMAGE_PROB = 0.6        # 发说说配图概率（GUI 可调；0=纯文字）
ACTION_GAP_MIN, ACTION_GAP_MAX = 3.0, 8.0  # 操作间随机间隔（秒）
POST_HOUR_START, POST_HOUR_END = 8, 23      # 每天发说说的时间窗口


def _feed_comment_prob() -> float:
    """好友动态评论概率（GUI 可调，默认 0.35；0=只点赞不评论）。"""
    return float(getattr(runtime, "QZONE_FEED_COMMENT_PROB", FEED_COMMENT_PROB) or 0)

# 去重种类
KIND_DAILY_POST = "daily_post"            # ref = 本地时间戳（统计每天发布条数）
KIND_POST_NEXT = "daily_post_next"        # ref = 下次可发时间（unix 秒，发布时随机锁定）
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
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
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

    注意：调用方（_get_conn）已持有 _lock，此处不再加锁（threading.Lock 不可重入）。
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


def _gap():
    return random.uniform(ACTION_GAP_MIN, ACTION_GAP_MAX)


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
        import httpx
        if image_path_or_url.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                r = await client.get(image_path_or_url)
                if r.status_code != 200:
                    logger.warning("视觉识别图片下载失败 HTTP %s", r.status_code)
                    return ""
                raw = r.content
                mime = r.headers.get("content-type", "image/jpeg").split(";")[0]
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


def _next_post_ts() -> float:
    """最近一次发布时锁定的"下次可发时间"（unix 秒）；没有锁定记录返回 0。

    注意：每次发布都会新增一条锁记录，这里必须取**最新**一条
    （ORDER BY rowid DESC），否则会读到最早那条早已过期的锁，
    导致条间间隔冷却永远不生效。
    """
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            "SELECT ref FROM qzone_processed WHERE kind = ? ORDER BY rowid DESC LIMIT 1",
            (KIND_POST_NEXT,),
        ).fetchone()
    if not row:
        return 0
    try:
        return float(row["ref"])
    except (TypeError, ValueError):
        return 0


def _lock_next_post(ts):
    """写入新的"下次可发时间"并清掉历史锁（只保留最新一条，表里不堆积）。"""
    conn = _get_conn()
    with _lock:
        conn.execute("DELETE FROM qzone_processed WHERE kind = ?", (KIND_POST_NEXT,))
        conn.execute(
            "INSERT INTO qzone_processed (kind, ref) VALUES (?, ?)",
            (KIND_POST_NEXT, str(int(ts))),
        )
        conn.commit()


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


async def send_qzone_update(api_call, llm_chat, image_gen_cb=None):
    """生成并发送一条空间说说；达每日上限/冷却中/窗口外返回空串。

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
    # 条间冷却：上次发布时在 [MIN, MAX] 区间随机锁定了下次可发时间
    next_ts = _next_post_ts()
    if next_ts and time.time() < next_ts:
        left = int((next_ts - time.time()) / 60)
        logger.info("空间说说: 冷却中（约 %d 分钟后可再发），跳过", left)
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
        # 多模态联动：情绪低落日，空间说说也带同款情绪（全平台状态一致）
        try:
            import liveness
            if liveness.today_mood_low():
                prompt += "\n（你今天心情不太好，这条说说的内容可以带一点点低落的日常感，但别写得太惨。）"
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
        if image_gen_cb and _post_image_prob() > 0 and random.random() < _post_image_prob():
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
        # 锁定下次可发时间：在 [MIN, MAX] 区间随机取一个间隔（0,0 或 max<=0 = 关闭冷却）
        lo, hi = _post_interval_range()
        if hi > 0:
            next_ts = int(time.time() + random.uniform(lo, hi) * 3600)
            _lock_next_post(next_ts)
            logger.info("空间说说: 下次可发时间已锁定（%d-%d 小时后，随机）", lo, hi)
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
                await api_call("comment_qzone", {"tid": tid, "content": reply,
                                                 "target_uin": str(self_uin or "")})
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
        key = str(feed.get("key") or "")
        uin = str(feed.get("uin") or "")
        if not key or uin == str(self_uin or ""):
            continue
        if "advertisement" in key or int(feed.get("appid") or 0) == 6600:
            continue  # 广告动态
        try:
            # 点赞（总是做，去重）
            if not is_processed(KIND_FEED_LIKED, key):
                await api_call("like_qzone", {"tid": key, "target_uin": uin})
                mark_processed(KIND_FEED_LIKED, key)
                liked += 1
                await asyncio.sleep(_gap())
            # 评论（按概率，去重）
            if (not is_processed(KIND_FEED_COMMENTED, key)
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
                        # 评论好友动态必须带 target_uin=好友 uin（说说主人），
                        # 缺参会报 retcode=100（SnowLuma CGI 需 hostuin 定位说说）
                        await api_call("comment_qzone", {"tid": key, "content": comment,
                                                         "target_uin": uin})
                        mark_processed(KIND_FEED_COMMENTED, key)
                        commented += 1
                        logger.info("已评论好友动态 [%s]: %s", uin, comment[:30])
                        await asyncio.sleep(_gap())
        except Exception as e:
            logger.warning("好友动态操作失败: %s", e)
        if liked + commented >= MAX_FEED_ACTIONS:
            break
    logger.info("好友动态检查完成: 点赞 %d 条, 评论 %d 条（本轮）", liked, commented)
    return liked, commented
