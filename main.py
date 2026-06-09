"""
kanobot — 舞萌 DX 查分 QQ Bot
基于 NoneBot2 + OneBot v11 协议
启动方式: python main.py
"""

import asyncio
import json
import os
import re
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# ══════════════════════════════════════════════════════
# 初始化 NoneBot2
# ══════════════════════════════════════════════════════

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

from datetime import datetime, timedelta

from nonebot import on_command, on_message, on_notice, logger
from nonebot.message import event_preprocessor
from nonebot.rule import Rule, endswith
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    MessageEvent,
    Message,
    MessageSegment,
    GroupMessageEvent,
    PokeNotifyEvent,
    Adapter as OneBotV11Adapter,
)
from nonebot.params import CommandArg

from api import MaimaiAPI, expand_charter_kw

from renderer import B50Renderer, FilteredScoreRenderer
from store import bind_user, unbind_user, get_token

api = MaimaiAPI()
_at_events = set()  # 记录由 @bot 触发的群聊消息，用于无效命令回退


# ══════════════════════════════════════════════════════
# 全局前缀 — 所有功能需加 kano 前缀
# ══════════════════════════════════════════════════════

@event_preprocessor
async def kano_prefix(event: Event):
    """群聊需 kano 前缀；私聊有无前缀均可"""
    if not isinstance(event, MessageEvent):
        return

    # 群聊 @bot 优先处理 — 即使无文本也要响应
    if isinstance(event, GroupMessageEvent):
        if event.is_tome():
            # 回复 bot 的消息不视为 @bot 调用（裸回复复读不算命令）
            if getattr(event, 'reply', None) is not None:
                return
            # 移除 @bot 的 at 段
            remaining = [seg for seg in event.message if seg.type != 'at']
            at_text = Message(remaining).extract_plain_text().strip()
            # 也清除文本中的 CQ at 码残留
            at_text = re.sub(r'\[CQ:at,qq=\d+\]', '', at_text).strip()
            m = re.match(r'(?i:kano)\s*(.+)', at_text)
            if m:
                at_text = m.group(1)
            if at_text:
                # @bot + 唤醒类词（呢/在吗/活着吗等）→ 等同唤醒
                if f'kano{at_text}' in _WAKE_WORDS:
                    event.message = Message(MessageSegment.text(f'kano{at_text}'))
                else:
                    event.message = Message(MessageSegment.text(at_text))
                    _at_events.add(id(event))
            else:
                event.message = Message(MessageSegment.text('kano呢'))
            return

    text = event.get_plaintext().strip()
    if not text:
        return

    # 唤醒词原样放行（不剥离前缀）
    if text.lower() in _WAKE_WORDS:
        return

    # 私聊 — 直接放行（无需前缀）
    if not isinstance(event, GroupMessageEvent):
        # 有 kano 前缀则剥离
        m = re.match(r'(?i:kano)\s*(.+)', text)
        if m:
            event.message = Message(MessageSegment.text(m.group(1)))
        return

    # 群聊 — 有 kano 前缀则剥离放行
    m = re.match(r'(?i:kano)\s*(.+)', text)
    if m:
        event.message = Message(MessageSegment.text(m.group(1)))
        return

    # 群聊无前缀 → 屏蔽命令类消息，避免多 Bot 误触
    # 排卡命令例外（仅群聊可用，无需前缀）
    if _parse_arcade_cmd(text) or text in ('排卡', 'j'):
        return
    first_word = text.split()[0].lower()
    # 也屏蔽「id11569」这种命令+数字粘连写法（NoneBot做前缀匹配）
    cmd_prefix = re.match(r'^([a-zA-Z一-鿿]+)(\d.*)?$', first_word)
    if first_word in _KNOWN_CMD_FIRST or text.endswith('是什么歌') \
       or (cmd_prefix and cmd_prefix.group(1) in _KNOWN_CMD_FIRST):
        event.message = Message(MessageSegment.text(''))
        return
    if _detect_compact_filter(text):
        event.message = Message(MessageSegment.text(''))
        return


# ══════════════════════════════════════════════════════
# 生命周期
# ══════════════════════════════════════════════════════

@driver.on_bot_connect
async def on_bot_connect(bot: Bot):
    logger.info(f"[CONNECT] Bot 已连接! self_id={bot.self_id}")


@driver.on_bot_disconnect
async def on_bot_disconnect(bot: Bot):
    logger.warning(f"[DISCONNECT] Bot 断开连接! self_id={bot.self_id}")
    # 强制清理 OneBot 适配器缓存的旧连接，防止重连时 403
    await asyncio.sleep(1)
    for adapter in driver._adapters.values():
        if isinstance(adapter, OneBotV11Adapter):
            bots = getattr(adapter, 'bots', {})
            stale_keys = [k for k in bots if str(k) == str(bot.self_id)]
            for k in stale_keys:
                bots.pop(k, None)
                logger.info(f"[DISCONNECT] 已清理适配器缓存: self_id={k}")


@driver.on_startup
async def on_startup():
    logger.info("kanobot 启动，正在加载歌曲库...")
    songs = await api.load_music_data()
    logger.info(f"kanobot — {len(songs)} 首歌曲已缓存")
    # 异步加载别名库（不阻塞启动）
    asyncio.create_task(_load_aliases_background())
    # 每日凌晨 4 点归零排卡
    asyncio.create_task(_daily_reset_arcade())

async def _load_aliases_background():
    try:
        aliases = await api.load_aliases()
        logger.info(f"kanobot — 别名库就绪: {len(aliases)} 个别名")
    except Exception as e:
        logger.warning(f"kanobot — 别名库加载失败: {e}")


async def _daily_reset_arcade():
    """每日凌晨 4:00 将所有机厅排卡人数归零"""
    while True:
        now = datetime.now()
        next4 = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now >= next4:
            next4 += timedelta(days=1)
        wait_seconds = (next4 - now).total_seconds()
        logger.info(f"[排卡] 下次归零: {next4.strftime('%Y-%m-%d %H:%M')} ({wait_seconds:.0f}s 后)")
        await asyncio.sleep(wait_seconds)

        now = datetime.now()
        now_str = now.strftime("%m/%d %H:%M")
        today = now.strftime("%Y%m%d")
        for name in _ARCADE_DEFS:
            old_history = _arcade_data.get(name, {}).get("history", []) if isinstance(_arcade_data.get(name), dict) else []
            _arcade_data[name] = {
                "count": 0,
                "updated": now_str,
                "history": [
                    h for h in old_history
                    if h.get("date") == today
                ] + [{
                    "time": now.strftime("%H:%M"),
                    "user": "系统归零",
                    "delta": 0,
                    "result": 0,
                    "date": today,
                }]
            }
        _save_arcade_data(_arcade_data)
        logger.info("[排卡] 每日凌晨 4 点 — 所有机厅人数已归零")


@driver.on_shutdown
async def on_shutdown():
    await api.close()
    logger.info("kanobot 已关闭")


# ══════════════════════════════════════════════════════
# 调试：消息日志
# ══════════════════════════════════════════════════════

msg_logger = on_message(priority=99, block=False)


@msg_logger.handle()
async def log_all_messages(bot: Bot, event: Event):
    if isinstance(event, MessageEvent):
        raw_text = event.get_plaintext()
        msg_type = "群聊" if isinstance(event, GroupMessageEvent) else "私聊"
        logger.info(f"[MSG] {msg_type} | 发送者:{event.user_id} | 内容: {raw_text}")


# ══════════════════════════════════════════════════════
# 复读 — 群聊消息被不同人复读 4 遍后跟复一遍
# ══════════════════════════════════════════════════════

# { group_id: { "current_text", "count", "users": set, "repeated": bool } }
# 不同文本打断即全重置，同一条消息可在新序列中再次复读
_repeat_tracker: dict[str, dict] = {}

# 不参与复读的消息段类型（图片/语音/视频/拍一拍/回复不计数）
_SKIP_REPEAT_SEG_TYPES = {"image", "record", "video", "poke", "reply"}


def _should_count_repeat(event: MessageEvent) -> bool:
    """判断消息是否应纳入复读计数"""
    # 回复消息不参与复读（引用文本会污染计数）
    if getattr(event, 'reply', None) is not None:
        return False
    text = event.get_plaintext().strip()
    if not text or len(text) < 2:
        return False
    for seg in event.message:
        if seg.type in _SKIP_REPEAT_SEG_TYPES:
            return False
    return True


repeat_msg = on_message(priority=97, block=False)


@repeat_msg.handle()
async def handle_repeat(bot: Bot, event: GroupMessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    if not _should_count_repeat(event):
        # 不可计数的消息（图片/回复/语音等）→ 中断复读链，重置计数
        group_id = str(event.group_id)
        if _repeat_tracker.get(group_id):
            del _repeat_tracker[group_id]
        return

    text = event.get_plaintext().strip()
    group_id = str(event.group_id)
    user_id = str(event.user_id)

    tracker = _repeat_tracker.get(group_id)

    if tracker and tracker["current_text"] == text:
        if user_id not in tracker["users"]:
            tracker["users"].add(user_id)
            tracker["count"] += 1
            if tracker["count"] >= 4 and not tracker["repeated"]:
                tracker["repeated"] = True
                await repeat_msg.send(MessageSegment.text(text))
    else:
        # 不同文本 → 全新序列，一切都重置
        _repeat_tracker[group_id] = {
            "current_text": text,
            "count": 1,
            "users": {user_id},
            "repeated": False,
        }


# ══════════════════════════════════════════════════════
# 指令注册
# ══════════════════════════════════════════════════════

b50_cmd    = on_command("b50", aliases={"查分", "B50"},           priority=5, block=True)
rating_cmd = on_command("rating", aliases={"rt", "Rating"},    priority=5, block=True)
info_cmd   = on_command("info",  aliases={"查歌", "歌曲", "id", "song"}, priority=5, block=True)
whatsong   = on_message(rule=endswith("是什么歌"),              priority=5, block=True)
rank_cmd   = on_command("rank",   aliases={"排名","排行"},       priority=5, block=True)
genres_cmd = on_command("genres", aliases={"分类"},             priority=5, block=True)

bind_cmd    = on_command("bind",    aliases={"绑定"},             priority=5, block=True)
unbind_cmd  = on_command("unbind",  aliases={"解绑"},             priority=5, block=True)
helper_cmd  = on_command("查分帮助", aliases={"kanohelp", "help"}, priority=5, block=True)


# ══════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════

def format_record_line(i: int, rec) -> str:
    """格式化单行成绩"""
    type_tag = "[DX]" if rec.type == "dx" else "[SD]"
    rate_label = rec.get_rate_label()
    fc_tag = f" {rec.get_fc_label()}" if rec.is_fc() else ""
    fs_tag = f" {rec.get_fs_label()}" if rec.is_sync() else ""
    return (
        f"#{i:>2} {type_tag} {rec.title} [{rec.level}]\n"
        f"    {rec.achievements}% | {rate_label}{fc_tag}{fs_tag} | RA:{rec.ra}"
    )


def build_player_summary(player) -> str:
    """构造玩家摘要"""
    msg = f"{player.nickname}"
    msg += f"  |  Rating: {player.rating}"
    msg += f"  |  DX: {player.dx_rating or '-'}"
    msg += f"  |  SD: {player.sd_rating or '-'}"
    if player.additional_rating:
        msg += f"  |  段位: Lv.{player.additional_rating}"
    if player.plate:
        msg += f"  |  {player.plate}"
    return msg


# ══════════════════════════════════════════════════
# 辅助：从绑定中解析当前用户
# ══════════════════════════════════════════════════

NOT_BOUND_MSG = (
    "❌ 未找到你的水鱼账号！\n\n"
    "方式一（推荐）: 在水鱼官网绑定 QQ 并设为公开\n"
    "  https://www.diving-fish.com/maimaidx/prober/ → 编辑个人资料 → 绑定QQ号\n"
    "  完成后直接发 kano b50 即可查分\n\n"
    "方式二（可选）: 私聊发送 bind <用户名> <Import-Token>\n"
    "  同一页面 → 生成 Import-Token\n"
    "  Token 可获取完整成绩（不限于B50），且不受隐私设置影响"
)

NEED_TOKEN_MSG = (
    "❌ 未找到你关联的水鱼账号！\n\n"
    "请去水鱼官网绑定 QQ 号：\n"
    "  https://www.diving-fish.com/maimaidx/prober/ → 编辑个人资料 → 绑定QQ号\n\n"
    "绑定后稍等片刻再试即可。"
)


async def resolve_username(event: MessageEvent) -> str:
    """解析水鱼用户名：本地绑定 → Dev-Token QQ查 → QQ公查"""
    t = get_token(str(event.user_id))
    if t:
        return t[0]

    qq = str(event.user_id)
    # Dev-Token：直接按 QQ 查完整成绩，拿到昵称
    try:
        profile = await api.get_dev_player_records(qq=qq)
        if profile and profile.nickname:
            return profile.nickname
    except Exception:
        pass

    # 无本地绑定 → 尝试 QQ 公查（仅 B50，受隐私设置影响）
    try:
        profile = await api.get_player_data(qq=qq)
        if profile and profile.nickname:
            return profile.nickname
    except Exception:
        pass

    return ""


# ══════════════════════════════════════════════════
# 查分指令（均自动使用已绑定的水鱼账号）
# ══════════════════════════════════════════════════

@b50_cmd.handle()
async def handle_b50(event: MessageEvent):
    """b50 — 生成 B50 成绩表图片（5列卡片网格，前7行B35 + 后3行B15）"""
    username = await resolve_username(event)
    if not username:
        await b50_cmd.finish(NOT_BOUND_MSG)

    if isinstance(event, GroupMessageEvent):
        await b50_cmd.send(f"正在生成 {username} 的 B50 成绩表...")

    # 优先用 QQ 公查（不需要 token，省配额）
    profile = await api.get_player_data(username=username)
    if not profile:
        profile = await api.get_player_data(qq=str(event.user_id))
    if not profile:
        await b50_cmd.finish(f"未找到玩家 [{username}]，或该玩家设置了隐私保护")

    if not profile.records:
        await b50_cmd.finish(f"{profile.nickname} 暂无成绩记录")

    try:
        song_ids = list({r.song_id for r in profile.records})
        covers = await api.download_covers(song_ids)

        renderer = B50Renderer(profile, covers=covers)
        renderer.render()
        img_bytes = renderer.to_bytes()
    except Exception as e:
        logger.error(f"生成B50图片失败: {e}")
        await b50_cmd.finish(f"生成图片失败: {e}")

    await b50_cmd.finish(
        MessageSegment.text(f"{profile.nickname} 的 B50 成绩表\n") +
        MessageSegment.image(img_bytes)
    )


@rating_cmd.handle()
async def handle_rating(event: MessageEvent):
    username = await resolve_username(event)
    if not username:
        await rating_cmd.finish(NOT_BOUND_MSG)

    # 优先用 QQ 公查（不需要 token，省配额）
    profile = await api.get_player_data(username=username)
    if not profile:
        profile = await api.get_player_data(qq=str(event.user_id))
    if not profile:
        await rating_cmd.finish(f"未找到玩家 [{username}]")

    stats = profile.statistics()
    msg = build_player_summary(profile) + "\n"
    msg += "────────────\n"
    msg += f"B50 最高 RA: {stats.get('max_ra', 0)}\n"
    msg += f"平均 RA: {stats.get('avg_ra', 0):.1f}\n"
    msg += "────────────\n"
    msg += f"SSS+: {stats.get('sssp_count', 0)}   "
    msg += f"SSS: {stats.get('sss_count', 0)}   "
    msg += f"SS: {stats.get('ss_count', 0)}\n"
    msg += f"AP: {stats.get('ap_count', 0)}   "
    msg += f"FC: {stats.get('fc_count', 0)}   "
    msg += f"总计: {stats.get('total', 0)}\n"
    msg += "────────────\n"
    msg += "各难度: "
    for lv in range(5):
        labels = ["B", "A", "E", "M", "Re"]
        msg += f"{labels[lv]}:{stats['by_level'].get(lv, 0)}  "

    await rating_cmd.finish(MessageSegment.text(msg))


# ══════════════════════════════════════════════════════
# 歌曲查询 — info <ID或曲名>  /  <曲名或ID>是什么歌
# ══════════════════════════════════════════════════════

def _format_song_compact(s) -> str:
    """紧凑单行歌曲信息（用于搜索结果列表）"""
    type_tag = "[DX]" if s.type == "dx" else "[SD]"
    new_tag = " 🆕" if s.is_new else ""
    ver_short = VERSION_NAME_TO_CODE.get(s.version, "")
    ver_str = f" | {ver_short}" if ver_short else f" | {s.version}"
    lines = [f"{type_tag} {s.title} (id={s.song_id}){ver_str}{new_tag}",
             f"  {s.artist} | {s.genre} | BPM:{s.bpm}"]
    chart_parts = []
    charters = []
    for i in range(min(5, len(s.charts))):
        c = s.charts.get(i)
        if c and c.ds > 0:
            short = LEVEL_LABEL_SHORT.get(c.level_label, c.level_label[:1])
            chart_parts.append(f"{short}:{c.level}({c.ds})")
            if c.charter:
                charters.append(c.charter)
    line3 = f"  {' / '.join(chart_parts)}"
    if charters:
        unique_charters = list(dict.fromkeys(charters))
        line3 += f"  |  🎨 {', '.join(unique_charters[:3])}"
        if len(unique_charters) > 3:
            line3 += " ..."
    lines.append(line3)
    return "\n".join(lines)


def _build_song_detail(song, records: list, username: str, has_token: bool = False) -> str:
    """构造歌曲详情（含谱面信息 + 可选个人成绩）"""
    type_tag = "[DX]" if song.type == "dx" else "[SD]"
    new_tag = " 🆕 新曲" if song.is_new else ""
    ver_short = VERSION_NAME_TO_CODE.get(song.version, "")
    ver_display = f"{song.version} ({ver_short})" if ver_short else song.version
    lines = [f"{type_tag} {song.title} (id={song.song_id}){new_tag}",
             f"艺术家: {song.artist}",
             f"分类: {song.genre}  |  BPM: {song.bpm}  |  版本: {ver_display}"]
    if song.release_date:
        lines.append(f"发布日期: {song.release_date}")
    lines.append("──── 谱面信息 ────")
    for i in range(min(5, len(song.charts))):
        c = song.charts.get(i)
        if c and c.ds > 0:
            line = f"{c.level_label}: {c.level} (定数 {c.ds})"
            if c.charter:
                line += f" — {c.charter}"
            lines.append(line)
    # 个人成绩
    if username and records:
        lines.append(f"──── {username} 的成绩 ────")
        for rec in sorted(records, key=lambda r: r.level_index):
            rate_label = rec.get_rate_label()
            fc_tag = f" {rec.get_fc_label()}" if rec.is_fc() else ""
            fs_tag = f" {rec.get_fs_label()}" if rec.is_sync() else ""
            lines.append(f"{rec.level_label}: {rec.achievements}% | {rate_label}{fc_tag}{fs_tag} | RA:{rec.ra}")
        best_ra = max(r.ra for r in records)
        lines.append(f"该曲最高 RA: {best_ra}")
    elif username and has_token:
        lines.append(f"──── {username} 暂无此曲成绩 ────")
    elif username and not has_token:
        lines.append("────\n⚠️ 未找到个人成绩。")
        lines.append("请去水鱼官网绑定QQ号: https://www.diving-fish.com/maimaidx/prober/")
    else:
        lines.append("────\n⚠️ 无法获取个人成绩。")
        lines.append("请去水鱼官网绑定QQ号: https://www.diving-fish.com/maimaidx/prober/")
    return "\n".join(lines)


async def _lookup_song(keyword: str, event: MessageEvent):
    """统一歌曲查询：ID → 别名 → 曲名/艺术家 → 详情或列表"""
    if not api._music_loaded:
        await api.load_music_data()

    # 纯数字 → 按 ID 查找
    if keyword.isdigit():
        song = api.get_song_by_id(keyword)
        if not song:
            return f"未找到歌曲 ID [{keyword}]", None

        # 查找同曲名的其它 ID（SD/DX 或不同版本）
        same_name = [
            s for s in api._song_list
            if str(s.song_id) != keyword and s.title.strip() == song.title.strip()
        ]
        if same_name:
            # 多版本聚合成列表
            all_versions = [song] + same_name
            msg = f"「{song.title}」有 {len(all_versions)} 个 ID\n────\n"
            for s in all_versions:
                msg += _format_song_compact(s) + "\n"
            msg += "────\n用 info <id> 查看具体版本详情"
            # 优先取第一个版本的封面
            covers = await api.download_covers([int(s.song_id)])
            cover = covers.get(s.song_id)
            return msg, cover

        username = await resolve_username(event)
        records = []
        t = get_token(str(event.user_id))
        if t:
            _, records = await api.get_song_player_record(
                keyword, username=username,
                import_token=t[1],
            )
        covers = await api.download_covers([int(s.song_id)])
        cover = covers.get(s.song_id)
        return _build_song_detail(song, records, username, has_token=bool(t)), cover

    # ── 别名搜索 ──
    alias_song_ids = api.search_by_alias(keyword)
    alias_songs: list = []
    for sid in alias_song_ids:
        song = api.get_song_by_id(str(sid))
        if song:
            # 标记匹配方式：精确别名 vs 模糊别名
            song._alias_match = "exact" if keyword.lower().strip() in [
                a.lower() for a in api.get_song_aliases(sid)
            ] else "partial"
            alias_songs.append(song)
    # 精确匹配优先
    alias_songs_exact = [s for s in alias_songs if getattr(s, '_alias_match', '') == 'exact']
    alias_songs_partial = [s for s in alias_songs if getattr(s, '_alias_match', '') != 'exact']

    # ── 曲名/艺术家搜索 ──
    text_results = api.search_songs(keyword, limit=10)

    # ── 合并去重 ──
    seen_ids: set[str] = set()
    combined: list = []
    for s in alias_songs_exact + alias_songs_partial:
        if s.song_id not in seen_ids:
            seen_ids.add(s.song_id)
            combined.append(s)
    for s in text_results:
        if s.song_id not in seen_ids:
            seen_ids.add(s.song_id)
            combined.append(s)

    if not combined:
        return f"未找到包含 [{keyword}] 的歌曲\n────\n试试搜别名: info <常用称呼>", None

    # 唯一结果 → 详情
    if len(combined) == 1:
        song = combined[0]
        username = await resolve_username(event)
        records = []
        t = get_token(str(event.user_id))
        if t:
            _, records = await api.get_song_player_record(
                song.song_id, username=username,
                import_token=t[1],
            )
        covers = await api.download_covers([int(song.song_id)])
        cover = covers.get(song.song_id)
        return _build_song_detail(song, records, username, has_token=bool(t)), cover

    # 多结果 → 列表（标注匹配来源）
    msg = f"搜索 [{keyword}] — {len(combined)} 首\n────\n"
    for s in combined:
        tag = ""
        if getattr(s, '_alias_match', None) == 'exact':
            tag = " [别名]"
        elif getattr(s, '_alias_match', None) == 'partial':
            tag = " [别名?]"
        msg += _format_song_compact(s).replace(
            f"{s.title} (id=", f"{s.title}{tag} (id=", 1
        ) + "\n"
    msg += "────\n用 info <id> 查看歌曲详情"
    return msg, None


@info_cmd.handle()
async def handle_info(event: MessageEvent, args: Message = CommandArg()):
    keyword = args.extract_plain_text().strip()
    if not keyword:
        await info_cmd.finish("用法: info <歌曲id或曲名>\n示例: info 11357  |  info ウミユリ")

    if isinstance(event, GroupMessageEvent):
        await info_cmd.send("正在查询...")

    msg, cover = await _lookup_song(keyword, event)
    if cover:
        await info_cmd.finish(MessageSegment.text(msg) + MessageSegment.image(cover))
    else:
        await info_cmd.finish(MessageSegment.text(msg))


@whatsong.handle()
async def handle_what_song(event: MessageEvent):
    raw = event.get_plaintext().strip()
    if raw.endswith("是什么歌"):
        keyword = raw[:-4].strip()
    else:
        return

    if not keyword:
        await whatsong.finish("用法: <歌曲id或曲名>是什么歌\n示例: 11357是什么歌  |  ウミユリ是什么歌")

    msg, cover = await _lookup_song(keyword, event)
    if cover:
        await whatsong.finish(MessageSegment.text(msg) + MessageSegment.image(cover))
    else:
        await whatsong.finish(MessageSegment.text(msg))

from models import (
    VERSION_CODES, VERSION_NAME_TO_CODE,
    GENRE_ALIASES,
    LEVEL_COLOR_TO_LABEL, LEVEL_LABEL_TO_COLOR, LEVEL_LABEL_SHORT,
    normalize_cjk,
)

# ══════════════════════════════════════════════════════
# Rating 排行（3.1.10）
# ══════════════════════════════════════════════════════

@rank_cmd.handle()
async def handle_rank(event: MessageEvent):
    username = await resolve_username(event)
    if not username:
        await rank_cmd.finish(NOT_BOUND_MSG)

    if isinstance(event, GroupMessageEvent):
        await rank_cmd.send(f"正在查询 {username} 的排名（数据量较大，请稍候）...")

    rank = await api.get_user_rank(username)
    if rank is None:
        await rank_cmd.finish(f"未找到 [{username}] 的排名（可能未公开或不存在）")

    profile = await api.get_player_data(username=username)
    rating = profile.rating if profile else "?"

    await rank_cmd.finish(
        f"{username}\n"
        f"Rating: {rating}\n"
        f"排名: #{rank}\n"
        f"────\n"
        f"数据来源: diving-fish 公开排行榜"
    )


# ══════════════════════════════════════════════════════
# 分类
# ══════════════════════════════════════════════════════

@genres_cmd.handle()
async def handle_genres():
    if not api._music_loaded:
        await api.load_music_data()
    genres = api.get_all_genres()
    msg = f"歌曲分类 ({len(genres)} 个)\n────\n"
    msg += "  ".join(genres)
    await genres_cmd.finish(MessageSegment.text(msg))


# ══════════════════════════════════════════════════════
# 条件筛选（分类/版本/难度/等级/定数/谱师/新歌）
# ══════════════════════════════════════════════════════

def _build_filter_summary(criteria: dict) -> str:
    """构造筛选条件摘要文字"""
    parts = []
    if criteria["genre"]:
        parts.append(f"分类:{criteria['genre']}")
    if criteria["version"]:
        ver_short = VERSION_NAME_TO_CODE.get(criteria["version"], criteria["version"])
        parts.append(f"版本:{ver_short}")
    if criteria["song_type"]:
        parts.append(f"{'DX' if criteria['song_type'] == 'dx' else 'SD'}")
    if criteria["level_label"]:
        color = LEVEL_LABEL_TO_COLOR.get(criteria["level_label"], criteria["level_label"])
        parts.append(f"{color}谱")
    if criteria["level_value"]:
        parts.append(f"{criteria['level_value']}级")
    if criteria["ds_value"] > 0:
        parts.append(f"定数{criteria['ds_value']}")
    if criteria["charter"]:
        parts.append(f"谱师:{criteria['charter']}")
    if criteria["is_new"]:
        parts.append("新歌")
    return " + ".join(parts) if parts else "全部"



# ══════════════════════════════════════════════════════
# 紧凑筛选 — 东方1350 / dx东方14+50 / 东方分数列表3
# ══════════════════════════════════════════════════════

# 已知筛选关键词（按长度降序确保贪婪匹配）
_COMPACT_KW_SOURCE = sorted(
    set(
        list(GENRE_ALIASES.keys()) +
        list(VERSION_CODES.keys()) +
        ['分数列表', 'dx', 'sd', '标准', '旧框', '新歌', '新曲',
         '绿', '黄', '红', '紫', '白',
         'Basic', 'Advanced', 'Expert', 'Master', 'Re:Master']
    ),
    key=len, reverse=True
)

# 正则：已知关键词 | 等级值(13/14+) | 定数值(14.3)
_COMPACT_TOKEN_RE = re.compile(
    '|'.join(re.escape(t) for t in _COMPACT_KW_SOURCE) +
    r'|\d{1,2}\+?|\d{1,2}\.\d',
    re.IGNORECASE
)

_KNOWN_CMD_FIRST = {
    'b50', '查分', 'rating', 'rt', 'info', '查歌', '歌曲', 'id', 'song',
    'rank', '排名', '排行', 'genres', '分类', 'bind', '绑定', 'unbind', '解绑',
    '查分帮助', 'kanohelp', 'help',
}


def _tokenize_compact(text: str) -> list[str]:
    """将紧凑文本拆分为筛选关键词列表"""
    return [m.group(0) for m in _COMPACT_TOKEN_RE.finditer(text)]


def _classify_compact_tokens(tokens: list[str], all_genres: list[str]) -> dict:
    """将关键词列表分类为筛选条件字典（返回值不含 mode/page，那些已提前提取）"""
    criteria: dict = {
        "genre": "", "version": "", "song_type": "",
        "level_label": "", "level_value": "",
        "ds_value": 0.0, "charter": "", "is_new": None,
    }

    for token in tokens:
        kw = token
        kw_lower = kw.lower()

        # 类型: dx / sd
        if kw_lower == "dx":
            criteria["song_type"] = "dx"; continue
        if kw_lower in ("sd", "标准", "旧框"):
            criteria["song_type"] = "sd"; continue

        # 难度颜色 → 标签
        if kw in LEVEL_COLOR_TO_LABEL:
            criteria["level_label"] = LEVEL_COLOR_TO_LABEL[kw]; continue
        for label in ("Basic", "Advanced", "Expert", "Master", "Re:Master"):
            if kw_lower == label.lower():
                criteria["level_label"] = label; break
        if criteria["level_label"]:
            continue

        # 新歌
        if kw in ("新歌", "新曲"):
            criteria["is_new"] = True; continue

        # 等级值 (13/14+/15)
        if re.match(r'^\d{1,2}\+?$', kw):
            criteria["level_value"] = kw; continue

        # 定数值 (14.3/13.9)
        if re.match(r'^\d{1,2}\.\d$', kw):
            criteria["ds_value"] = float(kw); continue

        # 分类别名
        genre_key = kw if kw in GENRE_ALIASES else (kw_lower if kw_lower in GENRE_ALIASES else None)
        if genre_key:
            criteria["genre"] = GENRE_ALIASES[genre_key]
            actual_lower = {g.lower(): g for g in all_genres}
            if criteria["genre"].lower() not in actual_lower:
                for g in all_genres:
                    if kw_lower in g.lower() or g.lower() in kw_lower:
                        criteria["genre"] = g; break
            continue
        # 分类名模糊匹配
        for g in all_genres:
            if kw_lower == g.lower() or kw_lower in g.lower() or g.lower() in kw_lower:
                criteria["genre"] = g; break
        if criteria["genre"]:
            continue

        # 版本代号
        if kw in VERSION_CODES:
            criteria["version"] = VERSION_CODES[kw]; continue
        for code, name in VERSION_CODES.items():
            if kw_lower == name.lower() or kw_lower in name.lower():
                criteria["version"] = name; break
        if criteria["version"]:
            continue

        # 剩余 → 谱师
        criteria["charter"] = kw

    return criteria


def _detect_compact_filter(text: str) -> bool:
    """判断文本是否为紧凑筛选命令"""
    if not text:
        return False
    # 排除已知命令前缀
    first_word = text.split()[0].lower()
    if first_word in _KNOWN_CMD_FIRST:
        return False
    # 排除 是什么歌 后缀
    if text.endswith('是什么歌'):
        return False

    # 包含「分数列表」→ 一定触发
    if '分数列表' in text:
        return True

    # 以 50 结尾 + 前面有非空文本（可能是谱师名等自由文本）
    if re.search(r'50$', text):
        return len(text[:-2].strip()) > 0

    # 有 2+ 类筛选关键词 → 触发
    tokens = _tokenize_compact(text)
    cats: set[str] = set()
    for t in tokens:
        tl = t.lower()
        if t in LEVEL_COLOR_TO_LABEL: cats.add('color')
        elif tl in GENRE_ALIASES: cats.add('genre')
        elif t in VERSION_CODES: cats.add('version')
        elif tl in ('dx', 'sd', '标准', '旧框'): cats.add('type')
        elif t in ('新歌', '新曲'): cats.add('new')
        elif re.match(r'^\d{1,2}\+?$', t) or re.match(r'^\d{1,2}\.\d$', t):
            cats.add('level')
    if len(cats) >= 2:
        return True
    return False


def _compact_filter_rule(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    return _detect_compact_filter(event.get_plaintext().strip())


async def _parse_compact(text: str) -> tuple[dict, str, int]:
    """
    解析紧凑筛选文本，返回 (criteria, mode, page)
    mode: "text" | "b50" | "score_list"
    """
    mode = "text"
    page = 1
    remaining = text

    # 提取「分数列表」+ 可选页码
    m = re.search(r'分数列表(\d*)$', remaining)
    if m:
        mode = "score_list"
        page = int(m.group(1)) if m.group(1) else 1
        remaining = remaining[:m.start()]
    elif re.search(r'[bB]?50$', remaining):
        mode = "b50"
        remaining = re.sub(r'[bB]?50$', '', remaining)

    if not api._music_loaded:
        await api.load_music_data()
    all_genres = api.get_all_genres() if api._song_list else []

    # 提取已知关键词 + 收集未匹配文本作为谱师
    tokens_raw = list(_COMPACT_TOKEN_RE.finditer(remaining))
    tokens = [m.group(0) for m in tokens_raw]
    gaps: list[str] = []
    last_end = 0
    for m in tokens_raw:
        if m.start() > last_end:
            gaps.append(remaining[last_end:m.start()])
        last_end = m.end()
    if last_end < len(remaining):
        gaps.append(remaining[last_end:])
    unmatched = ''.join(gaps).strip()

    criteria = _classify_compact_tokens(tokens, all_genres)
    if unmatched and not criteria["charter"]:
        criteria["charter"] = unmatched
    return criteria, mode, page


async def _fetch_player_full(qq: str):
    """获取玩家完整数据 — Dev-Token 优先，Import-Token 回落，无凭据返回 None"""
    # 优先：Developer-Token（无需用户手动绑定 Import-Token）
    full = await api.get_dev_player_records(qq=qq)
    if full and full.records:
        logger.info(f"[compact] Dev接口获取 {len(full.records)} 条成绩")
        return full
    if full is None and api._dev_token:
        # Dev-Token 有效但用户不存在 → 区分「查无此人」和「无 token」
        return None

    # 回落：Import-Token（旧方式）
    t = get_token(qq)
    if not t:
        return None
    _, token = t
    full = await api.get_player_full_records(import_token=token)
    if full and full.records:
        logger.info(f"[compact] Import-Token接口获取 {len(full.records)} 条成绩")
        return full
    return None


# ── 分数列表 消息处理器 ──
async def _handle_score_list(event: MessageEvent, criteria: dict, page: int):
    """处理 分数列表 请求：筛选+排序+分页+出图"""
    username = await resolve_username(event)
    if not username:
        await compact_msg.finish(NOT_BOUND_MSG)

    profile = await _fetch_player_full(str(event.user_id))
    if not profile:
        await compact_msg.finish(NEED_TOKEN_MSG)

    if isinstance(event, GroupMessageEvent):
        await compact_msg.send(f"正在生成 {username} 的成绩列表...")
    await api.enrich_records(profile)

    # 筛选
    filtered: list = []
    for rec in profile.records:
        song = rec.song_info
        if criteria["genre"] and song and song.genre.lower() != criteria["genre"].lower():
            continue
        if criteria["version"] and song and song.version.lower() != criteria["version"].lower():
            continue
        if criteria["song_type"] and rec.type != criteria["song_type"]:
            continue
        if criteria["level_label"] and rec.level_label != criteria["level_label"]:
            continue
        if criteria["level_value"]:
            if not (rec.level == criteria["level_value"] or rec.level.startswith(criteria["level_value"])):
                continue
        if criteria["ds_value"] > 0 and abs(rec.ds - criteria["ds_value"]) > 0.001:
            continue
        if criteria["charter"] and song:
            chart = song.charts.get(rec.level_index)
            if chart:
                kw_src = criteria["charter"].lower()
                kw_vars = {kw_src, normalize_cjk(kw_src, "traditional"), normalize_cjk(kw_src, "simplified")}
                kw_vars.update(expand_charter_kw(kw_src))
                matched = False
                if chart.charter and any(k in chart.charter.lower() for k in kw_vars):
                    matched = True
                else:
                    for note_name in (chart.notes or []):
                        if isinstance(note_name, str) and any(k in note_name.lower() for k in kw_vars):
                            matched = True; break
                if not matched:
                    continue
        if criteria["is_new"] and song and not song.is_new:
            continue
        filtered.append(rec)

    if not filtered:
        summary = _build_filter_summary(criteria)
        await compact_msg.finish(
            f"{username} [{summary}] — 无匹配成绩\n────\n试试放宽条件。"
        )
        return

    # 按达成率降序排列
    filtered.sort(key=lambda r: r.achievements, reverse=True)

    total_pages = (len(filtered) + 49) // 50
    page = max(1, min(page, total_pages))
    paged = filtered[(page - 1) * 50 : page * 50]

    summary = _build_filter_summary(criteria)
    title = f"{username}  |  筛选: {summary}" if summary else username

    # 渲染
    song_ids = list({r.song_id for r in paged})
    covers = await api.download_covers(song_ids)
    renderer = FilteredScoreRenderer(
        paged, username, covers=covers,
        layout="score", title=title,
        page=page, total_pages=total_pages,
        sort_by="achievements",
    )
    renderer.render()
    img_bytes = renderer.to_bytes()

    page_hint = f"（第 {page}/{total_pages} 张）" if total_pages > 1 else ""
    await compact_msg.finish(
        MessageSegment.text(
            f"{username} 按达成率排序 | {summary}{page_hint}\n"
            f"共 {len(filtered)} 首，翻页: 在原命令后加数字"
        ) +
        MessageSegment.image(img_bytes)
    )


# ── 紧凑筛选 消息处理器 ──

compact_msg = on_message(rule=Rule(_compact_filter_rule), priority=10, block=True)


@compact_msg.handle()
async def handle_compact_filter(event: MessageEvent):
    text = event.get_plaintext().strip()

    # 检测到 分数列表 → 走分数列表流程
    if '分数列表' in text:
        criteria, mode, page = await _parse_compact(text)
        await _handle_score_list(event, criteria, page)
        return

    # 解析紧凑筛选
    criteria, mode, page = await _parse_compact(text)

    # ── B50 图片模式 ──
    if mode == "b50":
        username = await resolve_username(event)
        if not username:
            await compact_msg.finish(
                f"❌ 条件B50需要绑定水鱼账号！\n\n"
                f"方式一（推荐）: 在水鱼官网绑定QQ并设为公开 → 直接 kano b50\n"
                f"方式二: 私聊 bind <用户名> <Import-Token>\n\n"
                f"不加 50 可只看曲库筛选（无需账号）"
            )

        profile = await _fetch_player_full(str(event.user_id))
        if not profile:
            await compact_msg.finish(NEED_TOKEN_MSG)

        if isinstance(event, GroupMessageEvent):
            await compact_msg.send(f"正在生成 {username} 的筛选B50...")
        await api.enrich_records(profile)

        # 筛选成绩
        filtered: list = []
        for rec in profile.records:
            song = rec.song_info
            if criteria["genre"] and song and song.genre.lower() != criteria["genre"].lower():
                continue
            if criteria["version"] and song and song.version.lower() != criteria["version"].lower():
                continue
            if criteria["song_type"] and rec.type != criteria["song_type"]:
                continue
            if criteria["level_label"] and rec.level_label != criteria["level_label"]:
                continue
            if criteria["level_value"]:
                if not (rec.level == criteria["level_value"] or rec.level.startswith(criteria["level_value"])):
                    continue
            if criteria["ds_value"] > 0 and abs(rec.ds - criteria["ds_value"]) > 0.001:
                continue
            if criteria["charter"] and song:
                chart = song.charts.get(rec.level_index)
                if chart:
                    kw_src = criteria["charter"].lower()
                    kw_vars = {kw_src, normalize_cjk(kw_src, "traditional"), normalize_cjk(kw_src, "simplified")}
                    kw_vars.update(expand_charter_kw(kw_src))
                    matched = False
                    if chart.charter and any(k in chart.charter.lower() for k in kw_vars):
                        matched = True
                    else:
                        for note_name in (chart.notes or []):
                            if isinstance(note_name, str) and any(k in note_name.lower() for k in kw_vars):
                                matched = True; break
                    if not matched:
                        continue
            if criteria["is_new"] and song and not song.is_new:
                continue
            filtered.append(rec)

        if not filtered:
            summary = _build_filter_summary(criteria)
            await compact_msg.finish(
                f"{username} 的筛选 [{summary}] — 无匹配成绩\n────\n试试放宽条件。"
            )
            return

        # 按版本新旧分栏：当前版本 top15 + 旧版本 top35
        # 从曲库实际数据中找出 VERSION_CODES 里排序最后且实际存在的版本
        all_song_vers = {s.version for s in api._song_list if s.version}
        current_ver = ""
        for ver in reversed(list(VERSION_CODES.values())):
            if ver in all_song_vers:
                current_ver = ver
                break
        if not current_ver:
            current_ver = list(VERSION_CODES.values())[-1]
        new_ver = [r for r in filtered if r.song_info and r.song_info.version == current_ver]
        old_ver = [r for r in filtered if r.song_info and r.song_info.version != current_ver]
        new_ver.sort(key=lambda r: r.ra, reverse=True)
        old_ver.sort(key=lambda r: r.ra, reverse=True)
        b15_recs = new_ver[:15]
        b35_recs = old_ver[:35]

        summary = _build_filter_summary(criteria)
        title = f"{username}  |  筛选: {summary}" if summary else username

        top50 = b35_recs + b15_recs
        song_ids = list({r.song_id for r in top50})
        covers = await api.download_covers(song_ids)
        renderer = FilteredScoreRenderer(
            top50, username, covers=covers,
            layout="b50", title=title, sort_by="ra",
            b35=b35_recs, b15=b15_recs,
        )
        try:
            renderer.render()
            img_bytes = renderer.to_bytes()
        except Exception as e:
            logger.error(f"生成筛选B50图片失败: {e}")
            await compact_msg.finish(f"生成图片失败: {e}")

        await compact_msg.finish(
            MessageSegment.text(
                f"{username} 的筛选B50 | {summary}\n"
                f"共 {len(top50)} 首 (筛选匹配 {len(filtered)} 首)"
            ) +
            MessageSegment.image(img_bytes)
        )
        return

    # ── 文字列表模式（无 50 / 无 分数列表）──
    songs = api.filter_songs(
        genre=criteria["genre"],
        version=criteria["version"],
        song_type=criteria["song_type"],
        level=criteria["level_value"],
        min_ds=criteria["ds_value"] if criteria["ds_value"] > 0 else 0.0,
        max_ds=criteria["ds_value"] if criteria["ds_value"] > 0 else 0.0,
        charter=criteria["charter"],
        is_new=criteria["is_new"],
    )

    if criteria["level_label"]:
        songs = [
            s for s in songs
            if any(c.level_label == criteria["level_label"] and c.ds > 0
                   for c in s.charts.values())
        ]

    summary = _build_filter_summary(criteria)
    if not songs:
        await compact_msg.finish(
            f"筛选 [{summary}] — 无匹配歌曲\n────\n试试换个关键词或查看: genres"
        )

    limit = min(len(songs), 15)
    msg = f"筛选 [{summary}] — {len(songs)} 首（显示前 {limit}）\n────\n"
    for s in songs[:limit]:
        type_tag = "[DX]" if s.type == "dx" else "[SD]"
        new_tag = " 🆕" if s.is_new else ""
        ver_short = VERSION_NAME_TO_CODE.get(s.version, "")
        ver_str = f" | {ver_short}" if ver_short else f" | {s.version}"
        msg += f"{type_tag} {s.title} (id={s.song_id}){ver_str}{new_tag}\n"
        msg += f"  {s.artist} | {s.genre} | BPM:{s.bpm}\n"
        chart_parts = []
        charters = []
        for i in range(min(5, len(s.charts))):
            c = s.charts.get(i)
            if c and c.ds > 0:
                short = LEVEL_LABEL_SHORT.get(c.level_label, c.level_label[:1])
                chart_parts.append(f"{short}:{c.level}({c.ds})")
                if c.charter:
                    charters.append(c.charter)
        msg += f"  {' / '.join(chart_parts)}"
        if charters:
            unique_charters = list(dict.fromkeys(charters))
            msg += f"  |  🎨 {', '.join(unique_charters[:3])}"
            if len(unique_charters) > 3:
                msg += " ..."
        msg += "\n"
    if len(songs) > limit:
        msg += f"──── 以上 {limit}/{len(songs)} 首，用 info <id> 查看详情 ────"

    await compact_msg.finish(MessageSegment.text(msg))


# ══════════════════════════════════════════════════════
# 排卡系统 — 机厅排队人数（仅群聊，无需 kano 前缀）
# ══════════════════════════════════════════════════════

_ARCADE_DEFS = {
    "天空之城": {"aliases": ["t", "tk", "tkzc"]},
    "时空龙骑士": {"aliases": ["龙", "龙骑士", "l"]},
    "大玩家": {"aliases": ["w", "d", "dwj", "wd"]},
    "真快活": {"aliases": ["z", "zkh", "花园城", "九方", "hyc", "jf"]},
    "PPG": {"aliases": ["ppg", "皮派阁", "九龙", "p"]},
    "壹零壹": {"aliases": ["101", "十里万达", "十里"]},
    "星世纪": {"aliases": ["x", "xsj", "浪井"]},
}

_ARCADE_ALIAS_MAP: dict[str, str] = {}
for _name, _info in _ARCADE_DEFS.items():
    for _a in _info["aliases"]:
        _ARCADE_ALIAS_MAP[_a.lower()] = _name
    _ARCADE_ALIAS_MAP[_name.lower()] = _name

_ARCADE_ALIASES_SORTED = sorted(_ARCADE_ALIAS_MAP.keys(), key=len, reverse=True)

_ARCADE_COUNTS_PATH = os.path.join(os.path.dirname(__file__), "data", "arcade_counts.json")
_MAX_QUEUE = 50


def _load_arcade_data() -> dict[str, dict]:
    if not os.path.exists(_ARCADE_COUNTS_PATH):
        return {}
    try:
        with open(_ARCADE_COUNTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_arcade_data(data: dict[str, dict]):
    os.makedirs(os.path.dirname(_ARCADE_COUNTS_PATH), exist_ok=True)
    with open(_ARCADE_COUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_arcade_count(name: str) -> int:
    entry = _arcade_data.get(name, {})
    val = entry.get("count", 0) if isinstance(entry, dict) else entry
    return int(val) if val else 0


def _get_arcade_updated(name: str) -> str:
    entry = _arcade_data.get(name, {})
    if isinstance(entry, dict):
        return entry.get("updated", "") or ""
    return ""


def _set_arcade_count(name: str, count: int, user: str = "", delta: int = 0):
    if name not in _arcade_data or not isinstance(_arcade_data.get(name), dict):
        _arcade_data[name] = {"count": 0, "updated": "", "history": []}
    entry = _arcade_data[name]
    if "history" not in entry:
        entry["history"] = []
    now = datetime.now()
    entry["count"] = count
    entry["updated"] = now.strftime("%m/%d %H:%M")
    # 记录历史（保留当天）
    today = now.strftime("%Y%m%d")
    entry["history"].append({
        "time": now.strftime("%H:%M"),
        "user": user or "?",
        "delta": delta,
        "result": count,
        "date": today,
    })
    # 只保留当天 + 最多 50 条
    entry["history"] = [h for h in entry["history"] if h.get("date") == today][-50:]
    _save_arcade_data(_arcade_data)


def _get_arcade_history(name: str) -> list[dict]:
    entry = _arcade_data.get(name, {})
    if isinstance(entry, dict):
        today = datetime.now().strftime("%Y%m%d")
        hist = entry.get("history", [])
        return [h for h in hist if h.get("date") == today]
    return []


def _get_user_name(event: GroupMessageEvent) -> str:
    """从群消息事件中获取用户显示名"""
    sender = event.sender
    card = getattr(sender, 'card', '') or getattr(sender, 'nickname', '')
    return str(card) if card else str(event.user_id)


_arcade_data = _load_arcade_data()
# 兼容旧格式（纯数字）→ 转为新格式
for _name in _ARCADE_DEFS:
    if _name not in _arcade_data:
        _arcade_data[_name] = {"count": 0, "updated": "", "history": []}
    elif not isinstance(_arcade_data[_name], dict):
        _arcade_data[_name] = {"count": int(_arcade_data[_name]), "updated": "", "history": []}
    if "history" not in _arcade_data[_name]:
        _arcade_data[_name]["history"] = []

_ARCADE_SUFFIX_RE = re.compile(r'^(几(?:[人卡])?|[+-]\d+|\+\+|--|\d+|j)$')


def _parse_arcade_cmd(text: str):
    """解析排卡命令。返回 (正式名, op, value) 或 None。op: query/set/add/sub"""
    t = text.strip().lower()
    for alias in _ARCADE_ALIASES_SORTED:
        if t.startswith(alias):
            suffix = t[len(alias):]
            m = _ARCADE_SUFFIX_RE.match(suffix)
            if not m:
                continue
            name = _ARCADE_ALIAS_MAP[alias]
            op_str = m.group(1)
            if op_str in ('j', '几', '几人', '几卡'):
                return (name, 'query', 0)
            elif op_str == '++':
                return (name, 'add', 1)
            elif op_str == '--':
                return (name, 'sub', 1)
            elif op_str.startswith('+'):
                return (name, 'add', int(op_str[1:]))
            elif op_str.startswith('-'):
                return (name, 'sub', int(op_str[1:]))
            else:
                return (name, 'set', int(op_str))
    return None


def _arcade_rule(event: Event) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    text = event.get_plaintext().strip()
    if text in ('排卡', 'j'):
        return True
    return _parse_arcade_cmd(text) is not None


arcade_msg = on_message(rule=Rule(_arcade_rule), priority=8, block=True)


def _format_history(name: str) -> str:
    """格式化当天最近 5 条加卡记录"""
    hist = _get_arcade_history(name)[-5:]
    if not hist:
        return ""
    lines = ["  ── 最近记录 ──"]
    for h in reversed(hist):
        d = h["delta"]
        if d > 0:
            delta_str = f"+{d}"
        elif d < 0:
            delta_str = str(d)
        else:
            delta_str = "→"
        lines.append(f"  {h['time']} {h['user']} {delta_str} = {h['result']}人")
    return "\n".join(lines)


@arcade_msg.handle()
async def handle_arcade(event: GroupMessageEvent):
    text = event.get_plaintext().strip()
    user_name = _get_user_name(event)

    # 查看所有机厅
    if text in ('排卡', 'j'):
        lines = ["🏢 各机厅排卡人数", "────────"]
        for name in _ARCADE_DEFS:
            count = _get_arcade_count(name)
            updated = _get_arcade_updated(name)
            bar = "▊" * min(count, 20)
            line = f"  {name}: {count}人 {bar}"
            if updated:
                line += f"  [{updated}]"
            lines.append(line)
        # 汇总当日最近记录
        all_hist: list[dict] = []
        for name in _ARCADE_DEFS:
            for h in _get_arcade_history(name):
                all_hist.append({**h, "arcade": name})
        all_hist.sort(key=lambda h: h["time"])
        if all_hist:
            lines.append("────────")
            lines.append("📋 当日最近加卡:")
            for h in all_hist[-5:]:
                d = h["delta"]
                if d > 0:
                    delta_str = f"+{d}"
                elif d < 0:
                    delta_str = str(d)
                else:
                    delta_str = "→"
                lines.append(f"  {h['time']} [{h['arcade']}] {h['user']} {delta_str} = {h['result']}人")
        await arcade_msg.finish(MessageSegment.text("\n".join(lines)))

    result = _parse_arcade_cmd(text)
    if not result:
        return

    name, op, value = result
    current = _get_arcade_count(name)
    updated = _get_arcade_updated(name)

    if op == 'query':
        extra = f"\n上次更新: {updated}" if updated else ""
        hist_text = _format_history(name)
        if hist_text:
            extra += "\n" + hist_text
        await arcade_msg.finish(
            MessageSegment.text(f"🏢 {name}: 当前 {current} 人排卡{extra}")
        )

    elif op == 'set':
        if value > _MAX_QUEUE:
            await arcade_msg.finish(
                MessageSegment.text(f"❌ 人数不能超过 {_MAX_QUEUE}！（当前 {current}，设置 {value}）")
            )
            return
        if value < 0:
            await arcade_msg.finish(
                MessageSegment.text(f"❌ 人数不能低于 0！（当前 {current}，设置 {value}）")
            )
            return
        _set_arcade_count(name, value, user=user_name, delta=value - current)
        arrow = "→" if value >= current else "←"
        await arcade_msg.finish(
            MessageSegment.text(f"✅ {name}: {current} {arrow} {value}人")
        )

    elif op == 'add':
        new_val = current + value
        if new_val > _MAX_QUEUE:
            await arcade_msg.finish(
                MessageSegment.text(f"❌ 人数不能超过 {_MAX_QUEUE}！（当前 {current}，+{value} = {new_val}）")
            )
            return
        _set_arcade_count(name, new_val, user=user_name, delta=value)
        await arcade_msg.finish(
            MessageSegment.text(f"✅ {name}: {current} → {new_val}人 (+{value})")
        )

    elif op == 'sub':
        new_val = current - value
        if new_val < 0:
            await arcade_msg.finish(
                MessageSegment.text(f"❌ 人数不能低于 0！（当前 {current}，-{value} = {new_val}）")
            )
            return
        _set_arcade_count(name, new_val, user=user_name, delta=-value)
        await arcade_msg.finish(
            MessageSegment.text(f"✅ {name}: {current} → {new_val}人 (-{value})")
        )


# ══════════════════════════════════════════════════════
# 账号绑定 / 成绩导入
# ══════════════════════════════════════════════════════

@bind_cmd.handle()
async def handle_bind(event: MessageEvent, args: Message = CommandArg()):
    """绑定水鱼 Import-Token — 仅限私聊"""
    if isinstance(event, GroupMessageEvent):
        await bind_cmd.finish(
            "⚠️ 请在私聊中使用此命令！\n"
            "Import-Token 是敏感凭据，不要在群聊中暴露。"
        )

    parts = args.extract_plain_text().strip().split(maxsplit=1)
    if len(parts) < 2:
        await bind_cmd.finish(
            "用法: bind <水鱼用户名> <Import-Token>\n"
            "示例: bind myname a1c0f27f254654ead...\n"
            "────\n"
            "📌 获取 Token:\n"
            "  1. 打开 https://www.diving-fish.com/maimaidx/prober/\n"
            "  2. 登录 → 编辑个人资料 → 生成 Import-Token\n"
            "  3. 复制 token，私聊发送: bind <用户名> <token>\n"
            "────\n"
            "🔒 安全: token 仅用于查询成绩，无法修改账号数据"
        )

    username, token = parts[0], parts[1]
    qq = str(event.user_id)

    # 验证 token 是否有效
    profile = await api.get_player_full_records(import_token=token)
    if not profile:
        await bind_cmd.finish(
            f"❌ Import-Token 验证失败！\n"
            f"用户名: {username}\n"
            f"请确认 token 是否正确，或重新生成。"
        )

    bind_user(qq, username, token)
    await bind_cmd.finish(
        f"✅ 绑定成功！\n"
        f"水鱼账号: {username}\n"
        f"现在可以使用 b50 / rating / info 等命令，\n"
        f"群聊发送 kano <命令> 即可查分。"
    )


@unbind_cmd.handle()
async def handle_unbind(event: MessageEvent):
    """解除绑定"""
    qq = str(event.user_id)
    t = get_token(qq)
    if not t:
        await unbind_cmd.finish("你还没有绑定水鱼账号。")

    unbind_user(qq)
    await unbind_cmd.finish(
        f"✅ 已解绑水鱼账号 [{t[0]}]。"
    )


# ══════════════════════════════════════════════════════
# 帮助
# ══════════════════════════════════════════════════════

@helper_cmd.handle()
async def handle_help():
    msg = (
        "kanobot 使用帮助\n"
        "💡 群内调用请在开头加上 kano\n"
        "────────────\n"
        "[查分]\n"
        "b50              — B50 成绩表图片\n"
        "rating / rt      — Rating 统计\n"
        "rank             — Rating 排名\n"
        "[歌曲]\n"
        "info <id或曲名>   — 歌曲详情 (已bind则含个人成绩)\n"
        "<xxx>是什么歌      — 同 info\n"
        "<条件..>50        — 条件筛选B50 (分类/版本/难度/等级/定数/谱师)\n"
        "                   示例: 东方b50 | dx东方14+b50 | 小鸟游b50\n"
        "genres           — 歌曲分类列表\n"
        "[账号]\n"
        "bind <用户> <token> — 绑定水鱼账号(私聊)\n"
        "                   获取Token: diving-fish → 编辑个人资料\n"
        "unbind            — 解绑水鱼账号\n"
        "[排卡] (仅群聊，无需前缀)\n"
        "排卡              — 查看所有机厅人数\n"
        "<机厅>j / 几 / 几人 — 查看指定机厅人数\n"
        "<机厅><数字>      — 设置人数 (≤50)\n"
        "<机厅>+<数字>     — 加人  <机厅>-<数字> — 减人\n"
        "  机厅: t=天空之城 l=龙骑士 w/d=大玩家\n"
        "        z=真快活 ppg=PPG 101=壹零壹 x=星世纪\n"
        "[其他]\n"
        "查分帮助           — 显示本帮助\n"
        "────────────\n"
        "数据来源: diving-fish 舞萌 DX API\n"
        "成绩导入教程: https://www.diving-fish.com/maimaidx/prober_guide"
    )
    await helper_cmd.finish(MessageSegment.text(msg))


# ══════════════════════════════════════════════════════
# 戳一戳回复
# ══════════════════════════════════════════════════════

poke = on_notice(priority=5, block=False)


@poke.handle()
async def handle_poke(event: PokeNotifyEvent):
    if event.target_id == event.self_id:
        await poke.finish(MessageSegment.text("干嘛。。。"))


# ══════════════════════════════════════════════════════
# 唤醒回复 — @ / kano在吗 等
# ══════════════════════════════════════════════════════

_WAKE_WORDS = {
    "kano在吗", "kano活着吗", "kano死了吗", "kano似了吗",
    "kano呢", "kano人呢",
}

_WAKE_REPLY = "kano在的哦，有事问我kanohelp吧QAQ"


def _is_wake_text(text: str) -> bool:
    return text.strip().lower() in _WAKE_WORDS


def _wake_rule(event: Event) -> bool:
    if not isinstance(event, MessageEvent):
        return False
    text = event.get_plaintext().strip().lower()

    # 被 @ 了（群聊中 at 本 bot）
    if isinstance(event, GroupMessageEvent):
        for seg in event.message:
            if seg.type == "at" and seg.data.get("qq") == str(event.self_id):
                if text:
                    return True
                return False

    # 匹配唤醒词
    if text in _WAKE_WORDS:
        return True
    return False


wake_msg = on_message(rule=Rule(_wake_rule), priority=12, block=True)


@wake_msg.handle()
async def handle_wake(event: MessageEvent):
    await wake_msg.finish(MessageSegment.text(_WAKE_REPLY))


# ══════════════════════════════════════════════════════
# @bot 无效命令回退 — @bot + 无法匹配的内容
# ══════════════════════════════════════════════════════

def _at_fallback_rule(event: Event) -> bool:
    if not isinstance(event, GroupMessageEvent):
        return False
    eid = id(event)
    if eid in _at_events:
        _at_events.discard(eid)
        return True
    return False

_at_fallback = on_message(rule=Rule(_at_fallback_rule), priority=99, block=True)

@_at_fallback.handle()
async def handle_at_fallback():
    await _at_fallback.finish(MessageSegment.text('不太对呢，试试给我发"help"吧'))


# ══════════════════════════════════════════════════════
# 启动
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    nonebot.run()
