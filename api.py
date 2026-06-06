"""
kanobot — 舞萌 DX API 客户端
基于 diving-fish 水鱼 API v3.1.x

参考文档: https://maimai.diving-fish.com/manual/docs/developer/zh-api-document/

端点一览:
  GET  /music_data               — 全曲目数据（公开，支持 ETag）
  POST /query/player              — 玩家成绩（公开，含 B50 简略数据）
  POST /query/plate               — 按版本获取成绩（公开）
  GET  /rating_ranking            — 全用户 Rating 排行（公开）
  拼接 /covers/{id}.png           — 歌曲封面（公开）
"""

import asyncio
import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("kanobot.api")

from models import (
    PlayerProfile,
    ScoreRecord,
    SongInfo,
    VERSION_CODES,
    normalize_cjk,
)


class MaimaiAPI:
    """舞萌 DX 查分 API 封装"""

    BASE_URL = "https://www.diving-fish.com/api/maimaidxprober"
    ALIAS_API = "https://www.yuzuchan.moe/api/maimaidx/maimaidxalias"
    ALIAS_CACHE = os.path.join(os.path.dirname(__file__), "cache", "music_alias.json")

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

        # 歌曲库缓存
        self._song_map: dict[str, "SongInfo"] = {}
        self._song_list: list["SongInfo"] = []
        self._music_loaded: bool = False
        self._music_etag: str = ""

        # 别名库缓存: alias_lower → [song_id, ...],  song_id → [alias, ...]
        self._alias_map: dict[str, list[int]] = {}
        self._song_aliases: dict[int, list[str]] = {}
        self._alias_loaded: bool = False

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ══════════════════════════════════════════════════
    # 3.1.4  歌曲数据（ETag 缓存）
    # ══════════════════════════════════════════════════

    async def load_music_data(self, force: bool = False) -> list[SongInfo]:
        """GET /music_data  支持 ETag 缓存，避免重复下载"""
        if self._music_loaded and not force:
            return self._song_list

        client = await self._get_client()
        headers = {}
        if self._music_etag:
            headers["If-None-Match"] = self._music_etag

        try:
            resp = await client.get(
                f"{self.BASE_URL}/music_data",
                headers=headers,
            )
            if resp.status_code == 304:
                logger.info("[API] 歌曲库未变化，使用缓存")
                self._music_loaded = True
                return self._song_list

            resp.raise_for_status()
            raw_list: list = resp.json()
        except httpx.HTTPError as e:
            logger.info(f"[API] 歌曲库加载失败: {e}")
            return []

        # 保存 ETag
        etag = resp.headers.get("etag", "")
        if etag:
            self._music_etag = etag

        self._song_map.clear()
        self._song_list.clear()

        for entry in raw_list:
            if isinstance(entry, dict):
                song = SongInfo.from_api(entry)
                self._song_map[song.song_id] = song
                self._song_list.append(song)

        self._music_loaded = True
        logger.info(f"[API] 歌曲库加载完成: {len(self._song_list)} 首")
        return self._song_list

    def get_song_by_id(self, song_id: str | int) -> Optional[SongInfo]:
        return self._song_map.get(str(song_id))

    def search_songs(self, keyword: str, limit: int = 20) -> list[SongInfo]:
        kw = keyword.lower()
        results = []
        for s in self._song_list:
            if kw in s.title.lower() or kw in s.artist.lower():
                results.append(s)
                if len(results) >= limit:
                    break
        return results

    def filter_songs(
        self, *, genre: str = "", version: str = "",
        song_type: str = "", min_ds: float = 0.0, max_ds: float = 0.0,
        level: str = "", charter: str = "", is_new: bool | None = None,
    ) -> list[SongInfo]:
        results = []
        # 浮点定数容差
        DS_EPSILON = 0.001
        # 是否做精确定数匹配（min==max 时要求同一谱面同时满足上下界）
        exact_ds = (min_ds > 0 and max_ds > 0 and abs(min_ds - max_ds) < DS_EPSILON)
        for s in self._song_list:
            if genre and s.genre.lower() != genre.lower():
                continue
            if version and s.version.lower() != version.lower():
                continue
            if song_type and s.type != song_type:
                continue
            if exact_ds:
                # 同一谱面必须同时满足 min 和 max（即定数 ≈ target）
                target = (min_ds + max_ds) / 2.0
                if not any(abs(d - target) <= DS_EPSILON for d in s.ds_list if d > 0):
                    continue
            else:
                if min_ds > 0 and not any(d >= min_ds - DS_EPSILON for d in s.ds_list if d > 0):
                    continue
                if max_ds > 0 and not any(d <= max_ds + DS_EPSILON for d in s.ds_list if d > 0):
                    continue
            if level:
                # 前缀匹配："13" 同时匹配 "13" 和 "13+"
                if not any(lv == level or lv.startswith(level) for lv in s.level_list):
                    continue
            if charter:
                # 搜索所有谱面的谱师（charter + notes，大小写不敏感，简繁自动兼容）
                found = False
                kw_src = charter.lower()
                # 生成简/繁两种形式覆盖日文汉字
                kw_variants = {kw_src, normalize_cjk(kw_src, "traditional"), normalize_cjk(kw_src, "simplified")}
                for c in s.charts.values():
                    # 检查 charter 字段
                    if c.charter:
                        c_lower = c.charter.lower()
                        if any(kw in c_lower for kw in kw_variants):
                            found = True
                            break
                    # 检查 notes 列表（API 有时只填 notes）
                    for note_name in (c.notes or []):
                        if isinstance(note_name, str):
                            n_lower = note_name.lower()
                            if any(kw in n_lower for kw in kw_variants):
                                found = True
                                break
                    if found:
                        break
                if not found:
                    continue
            if is_new is not None and s.is_new != is_new:
                continue
            results.append(s)
        return results

    def get_all_genres(self) -> list[str]:
        return sorted(set(s.genre for s in self._song_list if s.genre))

    def get_all_versions(self) -> list[str]:
        return sorted(set(s.version for s in self._song_list if s.version))

    # ══════════════════════════════════════════════════
    # 3.1.7  简略成绩（B50）
    # ══════════════════════════════════════════════════

    async def get_player_data(
        self, *, username: str = "", qq: str = "", b50: bool = True,
    ) -> Optional[PlayerProfile]:
        """POST /query/player  公开接口，受用户隐私设置影响"""
        if not username and not qq:
            raise ValueError("必须提供用户名或 QQ 号")

        client = await self._get_client()
        payload: dict = {}
        if username:
            payload["username"] = username
        else:
            payload["qq"] = qq
        if b50:
            payload["b50"] = "1"

        try:
            resp = await client.post(f"{self.BASE_URL}/query/player", json=payload)
            if resp.status_code == 400:
                logger.info(f"[API] 用户不存在: {username or qq}")
                return None
            elif resp.status_code == 403:
                logger.info(f"[API] 隐私设置不允许: {username or qq}")
                return None
            resp.raise_for_status()
            return PlayerProfile.from_api(resp.json())
        except httpx.HTTPError as e:
            logger.info(f"[API] 请求玩家数据失败: {e}")
            return None

    async def fetch_b50(self, username: str = "", qq: str = "") -> list[ScoreRecord]:
        profile = await self.get_player_data(username=username, qq=qq)
        return profile.get_best_n(50) if profile else []

    # ══════════════════════════════════════════════════
    # 3.1.8  按版本获取成绩（公开）
    # ══════════════════════════════════════════════════

    @staticmethod
    def get_version_codes() -> dict[str, str]:
        """返回 版本代号 → 版本名 的映射"""
        return dict(VERSION_CODES)

    @staticmethod
    def resolve_version_code(name_or_code: str) -> str:
        """
        解析版本参数：支持版本代号（如"熊"）或版本名（如"maimai でらっくす"）
        返回对应的版本代号
        """
        if name_or_code in VERSION_CODES:
            return name_or_code
        # 尝试反向查找
        from models import VERSION_NAME_TO_CODE
        return VERSION_NAME_TO_CODE.get(name_or_code, name_or_code)

    async def get_player_version_records(
        self, version_codes: list[str],
        *, username: str = "", qq: str = "",
    ) -> Optional[list[ScoreRecord]]:
        """
        POST /query/plate  公开接口（无需 token！）

        参数 version 必须是列表形式，元素为版本代号（如 "熊"、"祭" 等）
        返回该玩家在指定版本中已游玩的谱面成绩
        """
        if not username and not qq:
            raise ValueError("必须提供用户名或 QQ 号")

        client = await self._get_client()
        payload: dict = {"version": version_codes}
        if username:
            payload["username"] = username
        else:
            payload["qq"] = qq

        try:
            resp = await client.post(
                f"{self.BASE_URL}/query/plate",
                json=payload,
            )
            if resp.status_code == 400:
                logger.info(f"[API] 用户不存在: {username or qq}")
                return None
            elif resp.status_code == 403:
                logger.info(f"[API] 隐私设置不允许: {username or qq}")
                return None
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return [ScoreRecord.from_api(r) for r in data if isinstance(r, dict)]
            return []
        except httpx.HTTPError as e:
            logger.info(f"[API] 版本成绩请求失败: {e}")
            return None

    # ══════════════════════════════════════════════════
    # 3.1.5  完整成绩（登录验证 / Import-Token）
    # ══════════════════════════════════════════════════

    async def get_player_full_records(
        self, *, jwt_token: str = "", import_token: str = "",
    ) -> Optional[PlayerProfile]:
        """
        GET /player/records  获取已登录用户的完整成绩。
        需要 jwt_token（Cookie）或 import_token（Header）。
        返回包含全部 records 的 PlayerProfile，失败返回 None。
        """
        if not jwt_token and not import_token:
            raise ValueError("必须提供 jwt_token 或 import_token")

        client = await self._get_client()
        headers: dict = {}
        if import_token:
            headers["Import-Token"] = import_token
        # jwt_token 通过 Cookie 传递
        cookie = ""
        if jwt_token:
            cookie = f"jwt_token={jwt_token}"

        try:
            resp = await client.get(
                f"{self.BASE_URL}/player/records",
                headers={**headers, "Cookie": cookie} if cookie else headers,
            )
            if resp.status_code == 400:
                logger.info(f"[API] 完整成绩请求 token 有误")
                return None
            elif resp.status_code == 403:
                logger.info(f"[API] 完整成绩请求权限不足")
                return None
            resp.raise_for_status()
            return PlayerProfile.from_api(resp.json())
        except httpx.HTTPError as e:
            logger.info(f"[API] 完整成绩请求失败: {e}")
            return None

    async def get_song_player_record(
        self, song_id: str, *, username: str = "", qq: str = "",
        password: str = "", import_token: str = "",
    ) -> tuple[Optional[SongInfo], list[ScoreRecord]]:
        """
        查询某玩家在指定歌曲上的成绩。
        - 有 password/import_token → 调 /player/records 拿完整成绩后过滤（最全）
        - 无凭据 → B50 + 全版本 plate 兜底（公开接口，覆盖面有限）
        返回 (song, records)
        """
        if not self._music_loaded:
            await self.load_music_data()

        song = self.get_song_by_id(song_id)
        if not song:
            return None, []

        target_id = int(song_id)
        seen_levels: set[int] = set()
        matched: list[ScoreRecord] = []

        # ── 有凭据：拿完整成绩（全覆盖，无遗漏）──
        if username and (password or import_token):
            profile = None
            if import_token:
                profile = await self.get_player_full_records(import_token=import_token)
            elif password:
                jwt = await self.login(username, password)
                if jwt:
                    profile = await self.get_player_full_records(jwt_token=jwt)

            if profile and profile.records:
                for r in profile.records:
                    if r.song_id == target_id and r.level_index not in seen_levels:
                        seen_levels.add(r.level_index)
                        matched.append(r)
                return song, matched

        # ── 无凭据：B50 兜底 ──
        try:
            profile = await self.get_player_data(username=username, qq=qq)
            if profile and profile.records:
                for r in profile.records:
                    if r.song_id == target_id and r.level_index not in seen_levels:
                        seen_levels.add(r.level_index)
                        matched.append(r)
        except ValueError:
            pass

        # ── 无凭据：全版本 plate 兜底 ──
        if username or qq:
            try:
                all_codes = list(VERSION_CODES.keys())
                records = await self.get_player_version_records(
                    all_codes, username=username, qq=qq,
                )
                if records:
                    for r in records:
                        if r.song_id == target_id and r.level_index not in seen_levels:
                            seen_levels.add(r.level_index)
                            matched.append(r)
            except ValueError as e:
                logger.info(f"[API] get_song_player_record 全版本查询错误: {e}")

        return song, matched

    # ══════════════════════════════════════════════════
    # 3.1.9  歌曲封面
    # ══════════════════════════════════════════════════

    @staticmethod
    def get_cover_url(song_id: int | str) -> str:
        """
        返回歌曲封面图片 URL。
        ID 区间 10001~11000 的歌（DX 谱面）取其 SD 版封面（-10000）。
        """
        mid = int(song_id)
        if 10000 < mid <= 11000:
            mid -= 10000
        return f"https://www.diving-fish.com/covers/{mid:05d}.png"

    # ══════════════════════════════════════════════════
    # 3.1.10  Rating 排行
    # ══════════════════════════════════════════════════

    async def get_rating_ranking(self) -> list[dict]:
        """
        GET /rating_ranking
        返回所有公开用户的 username-ra 列表（未排序）
        """
        client = await self._get_client()
        try:
            resp = await client.get(f"{self.BASE_URL}/rating_ranking")
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.HTTPError as e:
            logger.info(f"[API] 排行榜获取失败: {e}")
            return []

    async def get_user_rank(self, username: str) -> Optional[int]:
        """
        查询指定用户的 Rating 排名（1-indexed）
        注意：数据量较大，不宜高频调用
        """
        ranking = await self.get_rating_ranking()
        if not ranking:
            return None
        sorted_ranking = sorted(
            ranking, key=lambda x: x.get("ra", 0), reverse=True
        )
        for i, entry in enumerate(sorted_ranking, 1):
            if entry.get("username") == username:
                return i
        return None

    # ══════════════════════════════════════════════════
    # 3.1.5 附  测试数据
    # ══════════════════════════════════════════════════

    async def get_test_data(self) -> Optional[PlayerProfile]:
        """GET /player/test_data  获取一份完整参考数据"""
        client = await self._get_client()
        try:
            resp = await client.get(f"{self.BASE_URL}/player/test_data")
            resp.raise_for_status()
            return PlayerProfile.from_api(resp.json())
        except httpx.HTTPError as e:
            logger.info(f"[API] 测试数据获取失败: {e}")
            return None

    # ══════════════════════════════════════════════════
    # 封面下载（本地缓存）
    # ══════════════════════════════════════════════════

    COVER_CACHE = os.path.join(os.path.dirname(__file__), "cache", "covers")

    async def download_covers(
        self, song_ids: list[int], max_concurrent: int = 12,
    ) -> dict[str, bytes]:
        """
        批量下载歌曲封面，优先读本地缓存。
        多源回退：diving-fish → 备选 CDN → 占位图
        返回 {song_id: raw_png_bytes}
        """
        os.makedirs(self.COVER_CACHE, exist_ok=True)
        result: dict[str, bytes] = {}
        sem = asyncio.Semaphore(max_concurrent)
        client = await self._get_client()

        def _cover_urls(sid: int) -> list[str]:
            """封面 URL 列表，按优先级排列"""
            mid = f"{sid:05d}"
            return [
                f"https://www.diving-fish.com/covers/{mid}.png",
                f"https://www.diving-fish.com/covers/{sid}.png",
            ]

        async def fetch_one(sid: int):
            async with sem:
                # 本地缓存命中
                cache_path = os.path.join(self.COVER_CACHE, f"{sid}.png")
                if os.path.exists(cache_path):
                    data = open(cache_path, "rb").read()
                    if data:
                        result[str(sid)] = data
                        return
                    # 缓存是空的（旧 bug 产物），删掉重下
                    os.remove(cache_path)

                # 多源尝试
                for url in _cover_urls(sid):
                    try:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        data = resp.content
                        if data and len(data) > 1000:  # 过滤 404 页面
                            result[str(sid)] = data
                            with open(cache_path, "wb") as f:
                                f.write(data)
                            return
                    except Exception:
                        continue

                # 所有源都失败
                result[str(sid)] = b""

        tasks = [fetch_one(sid) for sid in song_ids]
        await asyncio.gather(*tasks)
        return result

    # ══════════════════════════════════════════════════
    # 辅助
    # ══════════════════════════════════════════════════

    async def enrich_records(self, profile: PlayerProfile) -> PlayerProfile:
        """给成绩记录关联歌曲详情"""
        if not self._music_loaded:
            await self.load_music_data()
        for record in profile.records:
            record.song_info = self.get_song_by_id(record.song_id)
        return profile

    # ══════════════════════════════════════════════════
    # 别名库（yuzuchan.moe 公共 API + 本地缓存）
    # ══════════════════════════════════════════════════

    async def load_aliases(self, force: bool = False) -> dict[str, list[int]]:
        """
        从 yuzuchan.moe 拉取别名库，缓存到本地。
        返回 alias_lower → [song_id, ...] 映射。
        """
        if self._alias_loaded and not force:
            return self._alias_map

        client = await self._get_client()

        # ── 尝试远程 API ──
        try:
            resp = await client.get(self.ALIAS_API, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0 and data.get("content"):
                    content = data["content"]
                    os.makedirs(os.path.dirname(self.ALIAS_CACHE), exist_ok=True)
                    with open(self.ALIAS_CACHE, "w", encoding="utf-8") as f:
                        json.dump(content, f, ensure_ascii=False)
                    self._build_alias_index(content)
                    logger.info(f"[API] 别名库(远程)加载完成: {len(self._alias_map)} 个别名, "
                                f"{len(self._song_aliases)} 首歌曲")
                    return self._alias_map
        except Exception as e:
            logger.info(f"[API] 别名 API 请求失败: {e}，尝试本地缓存...")

        # ── 降级：本地缓存 ──
        if os.path.exists(self.ALIAS_CACHE):
            try:
                with open(self.ALIAS_CACHE, "r", encoding="utf-8") as f:
                    content = json.load(f)
                self._build_alias_index(content)
                logger.info(f"[API] 别名库(缓存)加载完成: {len(self._alias_map)} 个别名, "
                            f"{len(self._song_aliases)} 首歌曲")
                return self._alias_map
            except Exception as e:
                logger.info(f"[API] 别名缓存读取失败: {e}")

        logger.warning("[API] 别名库不可用，别名搜索将跳过")
        self._alias_loaded = True
        return {}

    def _build_alias_index(self, content: list):
        """构建别名 → 歌曲ID 的双向索引"""
        self._alias_map.clear()
        self._song_aliases.clear()
        for entry in content:
            sid = entry.get("SongID", 0)
            aliases = entry.get("Alias", [])
            if not sid or not aliases:
                continue
            self._song_aliases[sid] = aliases
            for alias in aliases:
                key = alias.lower().strip()
                if key:
                    self._alias_map.setdefault(key, []).append(sid)
        self._alias_loaded = True

    def search_by_alias(self, keyword: str) -> list[int]:
        """按别名搜索，返回匹配的 song_id 列表（精确优先，再模糊）"""
        if not self._alias_loaded:
            return []
        kw = keyword.lower().strip()
        seen: set[int] = set()
        results: list[int] = []

        def add(sid: int):
            if sid not in seen:
                seen.add(sid)
                results.append(sid)

        # 精确匹配优先
        if kw in self._alias_map:
            for sid in self._alias_map[kw]:
                add(sid)

        # 别名包含关键词
        for alias, sids in self._alias_map.items():
            if kw in alias:
                for sid in sids:
                    add(sid)

        return results

    def get_song_aliases(self, song_id: int | str) -> list[str]:
        """获取某首歌的所有别名"""
        return self._song_aliases.get(int(song_id), [])
