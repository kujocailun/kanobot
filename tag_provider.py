"""
谱面标签提供器 —— 从 dxrating.net 获取社区标注的谱面配置标签。

数据来源: https://dxrating.net (gekichumai/dxrating)
API端点: https://miruku.dxrating.net/api/v1/tags

标签体系（21 个标签，3 个分组）:
  - 配置组 (14标签): 交互/转圈/错位/扫键/散打/纵连/跳拍/爆发/拆弹/
                      定拍/大位移/反手/一笔画/绝赞段
  - 难度组 (2标签):  诈称谱(越级难)/水(虚高)
  - 评价组 (5标签):  星星谱(Slide多)/键盘谱(Tap多)/体力谱/底力谱/高物量

使用方式:
    provider = TagProvider()
    await provider.ensure_loaded()
    tags = provider.get_chart_tags(song_title, "dx", "master")
"""

import json
import logging
import os
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("kanobot.tag_provider")

# --- 数据结构 ---


@dataclass
class TagDef:
    id: int
    group_id: int
    name_zh: str
    name_en: str
    name_ja: str
    desc_zh: str


@dataclass
class TagGroup:
    id: int
    name_zh: str
    name_en: str
    color: str


@dataclass
class ChartTags:
    """一张谱面的标签集合"""
    song_id: str           # 歌曲标题（diving-fish title 字段）
    sheet_type: str        # "dx" / "std" / "utage"
    sheet_difficulty: str  # "basic" / "advanced" / "expert" / "master" / "remaster"
    tags: list[TagDef] = field(default_factory=list)


class TagProvider:
    """dxrating 标签数据提供器，带本地缓存。"""

    API_URL = "https://miruku.dxrating.net/api/v1/tags"
    CACHE_FILE = os.path.join(os.path.dirname(__file__), "dxrating_tags.json")
    CACHE_MAX_AGE = 86400 * 7  # 7 天刷新一次

    def __init__(self):
        self._tags: dict[int, TagDef] = {}
        self._groups: dict[int, TagGroup] = {}
        # key: (song_id, sheet_type, sheet_difficulty) → list[tag_id]
        self._chart_index: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        self._loaded = False

    # ---- 数据获取 ----

    def _fetch_remote(self) -> dict:
        """通过 curl 获取远程标签数据（绕过 Cloudflare TLS 指纹检测）。"""
        log.info("Fetching dxrating tags from %s", self.API_URL)
        result = subprocess.run(
            ["curl", "-s", self.API_URL,
             "-H", "Accept: application/json",
             "--max-time", "30"],
            capture_output=True, text=True, encoding="utf-8",
        )
        result.check_returncode()
        return json.loads(result.stdout)

    def _load_from_cache(self) -> Optional[dict]:
        if not os.path.exists(self.CACHE_FILE):
            return None
        age = time.time() - os.path.getmtime(self.CACHE_FILE)
        if age > self.CACHE_MAX_AGE:
            log.info("Cache expired (age=%.0fh), will refresh", age / 3600)
            return None
        with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
            log.info("Loaded tags from cache (age=%.0fh)", age / 3600)
            return json.load(f)

    def _save_cache(self, data: dict) -> None:
        with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("Saved %d tags to cache", len(data.get("tags", [])))

    # ---- 索引构建 ----

    def _build_index(self, raw: dict) -> None:
        """将原始 JSON 构建为查询索引。"""
        self._tags.clear()
        self._groups.clear()
        self._chart_index.clear()

        for g in raw.get("tagGroups", []):
            self._groups[g["id"]] = TagGroup(
                id=g["id"],
                name_zh=g["localized_name"].get("zh-Hans", g["localized_name"].get("en", "")),
                name_en=g["localized_name"].get("en", ""),
                color=g["color"],
            )

        for t in raw.get("tags", []):
            self._tags[t["id"]] = TagDef(
                id=t["id"],
                group_id=t["group_id"],
                name_zh=t["localized_name"].get("zh-Hans", t["localized_name"].get("en", "")),
                name_en=t["localized_name"].get("en", ""),
                name_ja=t["localized_name"].get("ja", ""),
                desc_zh=t["localized_description"].get("zh-Hans", ""),
            )

        for ts in raw.get("tagSongs", []):
            key = (ts["song_id"], ts["sheet_type"], ts["sheet_difficulty"])
            self._chart_index[key].append(ts["tag_id"])

        self._loaded = True

    # ---- 公开接口 ----

    async def ensure_loaded(self) -> None:
        """确保数据已加载（先读缓存，过期则拉远程）。"""
        if self._loaded:
            return

        data = self._load_from_cache()
        if data is None:
            data = self._fetch_remote()
            self._save_cache(data)

        self._build_index(data)

    def get_chart_tags(
        self, song_title: str, sheet_type: str = "dx", sheet_difficulty: str = "master"
    ) -> ChartTags:
        """获取一张谱面的所有标签。

        Args:
            song_title: 歌曲标题（diving-fish music_data 的 title 字段）
            sheet_type: "dx" / "std" / "utage"
            sheet_difficulty: "basic" / "advanced" / "expert" / "master" / "remaster"
        """
        if not self._loaded:
            raise RuntimeError("TagProvider not loaded — call await ensure_loaded() first")

        key = (song_title, sheet_type, sheet_difficulty)
        tag_ids = self._chart_index.get(key, [])

        return ChartTags(
            song_id=song_title,
            sheet_type=sheet_type,
            sheet_difficulty=sheet_difficulty,
            tags=[self._tags[tid] for tid in tag_ids if tid in self._tags],
        )

    def get_chart_tag_names(
        self, song_title: str, sheet_type: str = "dx", sheet_difficulty: str = "master"
    ) -> list[str]:
        """获取一张谱面的标签中文名列表（便捷方法）。"""
        chart = self.get_chart_tags(song_title, sheet_type, sheet_difficulty)
        return [t.name_zh for t in chart.tags]

    def get_chart_tags_from_record(self, record) -> ChartTags:
        """从 ScoreRecord 一键查标签（自动转换 type/difficulty 格式）。"""
        return self.get_chart_tags(
            record.title,
            normalize_type(record.type),
            normalize_difficulty(record.level_label),
        )

    def get_all_tags(self) -> list[TagDef]:
        """获取所有标签定义。"""
        if not self._loaded:
            raise RuntimeError("TagProvider not loaded")
        return list(self._tags.values())

    def get_all_groups(self) -> list[TagGroup]:
        """获取所有标签组。"""
        if not self._loaded:
            raise RuntimeError("TagProvider not loaded")
        return list(self._groups.values())

    def get_tag_by_name(self, name_zh: str) -> Optional[TagDef]:
        """按中文名查标签定义。"""
        for t in self._tags.values():
            if t.name_zh == name_zh:
                return t
        return None

    def get_tagged_songs(self, tag_name_zh: str) -> list[str]:
        """获取被打上某标签的所有曲目标题。"""
        tag = self.get_tag_by_name(tag_name_zh)
        if tag is None:
            return []
        return sorted(set(
            song_id for (song_id, _, _), tag_ids in self._chart_index.items()
            if tag.id in tag_ids
        ))

    # ---- 底力分析 ----

    def analyze_player_skills(
        self,
        b50_records: list[dict],
    ) -> dict[str, dict]:
        """
        基于 B50 记录和谱面标签，计算玩家各配置类型的能力分。

        Args:
            b50_records: B50 记录列表，每条需包含:
                - title (歌曲标题)
                - type ("dx"/"std")
                - difficulty ("master"等)
                - achievements (达成率, 0~101)
                - rating (单曲 Rating)

        Returns:
            {tag_name_zh: {count, total_score, avg_achievement, skill_level, top_song}}
        """
        if not self._loaded:
            raise RuntimeError("TagProvider not loaded")

        # 聚合：按标签分组
        tag_stats: dict[int, dict] = defaultdict(lambda: {
            "count": 0,
            "total_achievement": 0.0,
            "max_rating": 0,
            "top_song": "",
        })

        for rec in b50_records:
            chart = self.get_chart_tags(
                rec.get("title", ""),
                rec.get("type", "dx"),
                rec.get("difficulty", "master"),
            )
            for tag in chart.tags:
                s = tag_stats[tag.id]
                s["count"] += 1
                s["total_achievement"] += rec.get("achievements", 0)
                if rec.get("rating", 0) > s["max_rating"]:
                    s["max_rating"] = rec["rating"]
                    s["top_song"] = rec.get("title", "")

        # 计算能力分
        result = {}
        for tag_id, s in tag_stats.items():
            tag = self._tags.get(tag_id)
            if tag is None or s["count"] < 2:
                continue

            avg_ach = s["total_achievement"] / s["count"]
            # 能力分 = 平均达成率 (0~100) × 浓度加权
            # 浓度 = 该标签曲在 B50 中的占比
            concentration = s["count"] / len(b50_records) if b50_records else 0
            skill = avg_ach * (0.5 + 0.5 * min(concentration * 10, 1.0))

            result[tag.name_zh] = {
                "count": s["count"],
                "avg_achievement": round(avg_ach, 2),
                "concentration": round(concentration, 3),
                "skill_level": round(skill, 1),
                "max_rating": s["max_rating"],
                "top_song": s["top_song"],
                "group": self._groups.get(tag.group_id, TagGroup(0, "", "", "")).name_zh,
            }

        return result


    # ---- 反查 & 推荐 ----

    def find_charts_by_tag(self, tag_name_zh: str) -> list[tuple[str, str, str]]:
        """反查某标签下的全部谱面。

        Returns:
            list of (song_title, sheet_type, sheet_difficulty)
        """
        tag = self.get_tag_by_name(tag_name_zh)
        if tag is None:
            return []
        return sorted(set(
            key for key, tag_ids in self._chart_index.items()
            if tag.id in tag_ids
        ))

    def recommend_charts(
        self,
        tag_name: str,
        player_records: list,
        song_db: list,
        limit: int = 50,
        center_ds: float | None = None,
    ) -> list["PracticeRecommendation"]:
        """基于玩家水平和指定配置标签，推荐练习谱面。

        Args:
            tag_name: 标签中文名（如 "交互"）
            player_records: 玩家全部成绩列表 (ScoreRecord)
            song_db: 全曲目数据列表 (SongInfo)
            limit: 返回数量（默认 50）
            center_ds: 推荐中心定数。None 则自动从玩家 B50 RA 推算
                       （ceiling_ra / 22.4 = SSS+ 对应定数）。

        Returns:
            按 priority_score 降序排列的推荐列表
        """
        import math

        # 1. 反查该 tag 下所有谱面
        charts = self.find_charts_by_tag(tag_name)
        if not charts:
            return []

        # 2. 构建玩家成绩索引: (title, type, difficulty) → ScoreRecord
        player_index: dict[tuple[str, str, str], any] = {}
        for rec in player_records:
            key = (rec.title, normalize_type(rec.type), normalize_difficulty(rec.level_label))
            # 同一谱面保留 RA 更高的记录
            if key not in player_index or rec.ra > player_index[key].ra:
                player_index[key] = rec

        # 3. 推荐中心定数（外部传入 = ceiling_ra / 22.4，即天花板 SSS+ 对应定数）
        if center_ds is None:
            b50 = sorted(player_records, key=lambda r: r.ra, reverse=True)[:50]
            b50_ra = [r.ra for r in b50 if r.ra > 0]
            if b50_ra:
                center_ds = b50_ra[-1] / 22.4  # ceiling RA → SSS+ ds
            else:
                center_ds = 13.0

        # 4. 构建 song_db 标题索引: title → SongInfo
        title_to_song: dict[str, any] = {}
        for s in song_db:
            if s.title and s.title not in title_to_song:
                title_to_song[s.title] = s

        # 5. difficulty → level_index 映射
        diff_to_idx = {
            "basic": 0, "advanced": 1, "expert": 2,
            "master": 3, "remaster": 4,
        }

        # 6. 为每个谱面计算推荐分
        sigma = 2.0  # Gaussian σ
        results: list[PracticeRecommendation] = []

        for title, stype, sdiff in charts:
            song = title_to_song.get(title)
            if song is None:
                continue

            idx = diff_to_idx.get(sdiff)
            if idx is None or idx not in song.charts:
                continue

            chart_info = song.charts[idx]
            chart_ds = chart_info.ds
            if chart_ds <= 0:
                continue

            # ds_proximity — Gaussian 衰减
            ds_diff = chart_ds - center_ds
            ds_proximity = 100.0 * math.exp(-(ds_diff ** 2) / (2 * sigma ** 2))

            # achievement_weight — 连续函数
            player_key = (title, stype, sdiff)
            existing = player_index.get(player_key)
            if existing is None or existing.achievements < 99.0:
                ach_weight = 1.0
            else:
                ach_weight = max(0.0, (100.75 - existing.achievements) / (100.75 - 99.0))

            priority_score = ds_proximity * ach_weight

            # 该谱面所有标签
            chart_tags = self.get_chart_tags(title, stype, sdiff)
            all_tag_names = [t.name_zh for t in chart_tags.tags]

            results.append(PracticeRecommendation(
                title=title,
                song_id=song.song_id,
                sheet_type=stype,
                sheet_difficulty=sdiff,
                level=chart_info.level,
                ds=chart_ds,
                all_tags=all_tag_names,
                target_tag=tag_name,
                played=existing is not None,
                current_achievement=existing.achievements if existing else 0.0,
                priority_score=round(priority_score, 2),
            ))

        # 7. 排序取 top N
        results.sort(key=lambda r: r.priority_score, reverse=True)
        return results[:limit]

    def fuzzy_match_tag(self, keyword: str) -> list[str]:
        """模糊匹配标签名 → 返回匹配的标签中文名列表（精确 > 前缀 > 包含）"""
        all_names = [t.name_zh for t in self._tags.values()]
        kw = keyword.strip()

        # 精确匹配
        exact = [n for n in all_names if n == kw]
        if exact:
            return exact

        # 前缀匹配
        prefix = [n for n in all_names if n.startswith(kw)]
        if prefix:
            return prefix

        # 包含匹配
        contain = [n for n in all_names if kw in n]
        return contain


# ---- 格式转换 ----

def normalize_type(t: str) -> str:
    """diving-fish type → dxrating sheet_type"""
    return "std" if t == "sd" else t


def normalize_difficulty(d: str) -> str:
    """diving-fish level_label → dxrating sheet_difficulty"""
    return d.lower()


# ---- 推荐结果 ----

@dataclass
class PracticeRecommendation:
    """练习推荐条目"""
    title: str                 # 曲名
    song_id: str               # diving-fish song_id (str)
    sheet_type: str            # "dx" / "std"
    sheet_difficulty: str      # "master" / "expert" / ...
    level: str                 # "13+"
    ds: float                  # 定数
    all_tags: list[str]        # 该谱面所有标签中文名
    target_tag: str            # 用户想练的标签
    played: bool               # 玩家是否已游玩
    current_achievement: float # 已有达成率（未游玩=0）
    priority_score: float      # 推荐优先级（越高越推荐）


# ---- 单例 ----

_tag_provider: Optional[TagProvider] = None


def get_tag_provider() -> TagProvider:
    global _tag_provider
    if _tag_provider is None:
        _tag_provider = TagProvider()
    return _tag_provider
