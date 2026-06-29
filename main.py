"""
StalZone Clan Bot v3.2 - Полная переработка
Трекинг ВСЕХ голосовых каналов и КВ (по этапам)
(StalZone — бывший STALCRAFT)
"""

import discord
from discord.ext import commands, tasks
import json
import logging
import asyncio
import aiosqlite
import os
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Optional, Dict, List, Any
from ranks import RANK_ORDER, DEFAULT_RANK
import pytz

# ============================================
# УТИЛИТЫ ФОРМАТИРОВАНИЯ ДАТ
# ============================================

def format_date(date_obj=None, date_str=None) -> str:
    """Форматирует дату в DD-MM-YYYY"""
    if date_obj:
        return date_obj.strftime('%d-%m-%Y')
    if date_str:
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return dt.strftime('%d-%m-%Y')
        except:
            return date_str
    return datetime.now().strftime('%d-%m-%Y')

def parse_date(date_str: str) -> Optional[datetime]:
    """Парсит дату из DD-MM-YYYY или YYYY-MM-DD"""
    for fmt in ('%d-%m-%Y', '%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None

def date_for_db(date_obj=None) -> str:
    """Возвращает дату в формате для БД (YYYY-MM-DD)"""
    if date_obj:
        return date_obj.strftime('%Y-%m-%d')
    return datetime.now().strftime('%Y-%m-%d')

def format_duration(seconds: int) -> str:
    """Форматирует длительность в читаемый вид"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}ч {minutes}м"
    elif minutes > 0:
        return f"{minutes}м {secs}с"
    else:
        return f"{secs}с"

# ============================================
# ФИКСИРОВАННОЕ РАСПИСАНИЕ КВ (StalZone)
# ============================================
# Расписание постоянное: его нельзя добавить или изменить командами.
# weekday(): 0=Пн, 1=Вт, 2=Ср, 3=Чт, 4=Пт, 5=Сб, 6=Вс. Все бои идут 20:00–21:00.
# Воскресенье — выбор между «Потасовка» и «Захват базы» (считается одно событие).

SUNDAY_EVENT_CHOICES = ['Потасовка', 'Захват базы']

FIXED_SCHEDULE = [
    {'id': 1, 'name': 'Потасовка', 'start_time': '20:00', 'end_time': '21:00', 'days_of_week': [0], 'notify_before': 15},
    {'id': 2, 'name': 'Потасовка', 'start_time': '20:00', 'end_time': '21:00', 'days_of_week': [1], 'notify_before': 15},
    {'id': 3, 'name': 'Потасовка', 'start_time': '20:00', 'end_time': '21:00', 'days_of_week': [2], 'notify_before': 15},
    {'id': 4, 'name': 'Турнир', 'start_time': '20:00', 'end_time': '21:15', 'days_of_week': [3], 'notify_before': 15},
    {'id': 5, 'name': 'Турнир', 'start_time': '20:00', 'end_time': '21:15', 'days_of_week': [4], 'notify_before': 15},
    {'id': 6, 'name': 'Турнир', 'start_time': '20:00', 'end_time': '21:15', 'days_of_week': [5], 'notify_before': 15},
    {'id': 7, 'name': 'Потасовка / Захват базы', 'start_time': '20:00', 'end_time': '21:00',
     'days_of_week': [6], 'notify_before': 15, 'choices': SUNDAY_EVENT_CHOICES},
]

# Этапы КВ по типу события: список (начало, конец). Присутствие на этапе
# считается по голосовым сессиям, пересекающимся с окном этапа.
# Захват базы — данные будут добавлены позже.
EVENT_STAGES = {
    'Потасовка': [('20:00', '20:20'), ('20:20', '20:40'), ('20:40', '21:00')],
    'Турнир':    [('20:00', '20:25'), ('20:25', '20:50'), ('20:50', '21:15')],
    # 'Захват базы': [...]  # TODO: добавить, когда будут данные
}

# ============================================
# КОНФИГУРАЦИЯ И ЛОГИРОВАНИЕ
# ============================================

def load_config():
    """Загружает базовую конфигурацию"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.critical("❌ config.json не найден!")
        raise SystemExit(1)
    except json.JSONDecodeError as e:
        logging.critical(f"❌ Ошибка парсинга config.json: {e}")
        raise SystemExit(1)

config = load_config()

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, config.get('LOG_LEVEL', 'INFO')),
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('ClanBot')


# ============================================
# ОСНОВНОЙ КЛАСС БОТА
# ============================================

class ClanBot(commands.Bot):
    """Основной класс бота v3.0"""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True
        intents.guilds = True
        
        super().__init__(
            command_prefix=config.get('PREFIX', '!'),
            intents=intents,
            help_command=None
        )
        
        self.config = config
        self.db: aiosqlite.Connection = None
        self.start_time = datetime.now()
        self.timezone = pytz.timezone(config.get('TIMEZONE', 'Europe/Moscow'))
        
        # Кэш настроек гильдий
        self.guild_settings: Dict[int, Dict] = {}
        self.guild_roles: Dict[int, Dict] = {}

        # Ручные звания, назначенные в боте
        # Ключ: "guild_id:user_id" -> звание (ключ из RANK_ORDER)
        self.member_ranks: Dict[str, str] = {}

        # Выбор воскресного события (Потасовка / Захват базы)
        # Ключ: "guild_id:YYYY-MM-DD" -> название события
        self.sunday_choices: Dict[str, str] = {}
        
        # Активные сессии (ВСЕ голосовые каналы)
        self.all_voice_sessions: Dict[str, Dict] = {}  # {"guild:user": {join_time, channel_id, ...}}

    async def setup_hook(self):
        """Инициализация при запуске"""
        logger.info("🔧 Инициализация бота v3.0...")
        
        Path('data').mkdir(exist_ok=True)
        Path('cogs').mkdir(exist_ok=True)
        
        await self.init_database()
        
        cogs = ['cogs.attendance', 'cogs.stalcraft', 'cogs.admin']
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"✅ Загружен: {cog}")
            except Exception as e:
                logger.error(f"❌ Ошибка загрузки {cog}: {e}")
        
        logger.info("🔧 Setup завершён")
    
    async def init_database(self):
        """Создание структуры БД v3.0"""
        self.db = await aiosqlite.connect(config.get('DB_PATH', 'data/bot_database.db'))
        
        await self.db.executescript('''
            -- ========================================
            -- НАСТРОЙКИ ГИЛЬДИЙ
            -- ========================================
            
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                kv_vc_channel_id INTEGER DEFAULT NULL,      -- VC для КВ
                report_channel_id INTEGER DEFAULT NULL,     -- Канал отчётов
                log_channel_id INTEGER DEFAULT NULL,        -- Канал логов
                sunday_default TEXT DEFAULT NULL,           -- Выбор по умолчанию для воскресений
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Роли сервера
            CREATE TABLE IF NOT EXISTS guild_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                role_type TEXT NOT NULL,
                role_id INTEGER NOT NULL,
                role_name TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, role_type, role_id)
            );

            -- Ручные звания участников (приоритетнее ролей Discord)
            CREATE TABLE IF NOT EXISTS member_ranks (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                rank TEXT NOT NULL,
                set_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            );
            
            -- ========================================
            -- ВЫБОР ВОСКРЕСНОГО СОБЫТИЯ (Потасовка / Захват базы)
            -- ========================================
            -- Расписание КВ фиксированное (в коде, FIXED_SCHEDULE).
            -- В таблице хранится только выбор события на конкретное воскресенье.

            CREATE TABLE IF NOT EXISTS kv_event_choice (
                guild_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                event_name TEXT NOT NULL,
                chosen_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, date)
            );

            -- ========================================
            -- СЕССИИ - ВСЕ ГОЛОСОВЫЕ КАНАЛЫ
            -- ========================================
            
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                display_name TEXT,
                channel_id INTEGER NOT NULL,
                channel_name TEXT,
                join_time TIMESTAMP NOT NULL,
                leave_time TIMESTAMP,
                duration_seconds INTEGER DEFAULT 0,
                date TEXT NOT NULL,
                status TEXT DEFAULT 'completed'
            );
            
            -- ========================================
            -- ПОСЕЩАЕМОСТЬ КВ
            -- ========================================
            
            CREATE TABLE IF NOT EXISTS kv_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                schedule_id INTEGER,
                date TEXT NOT NULL,
                kv_time TEXT,  -- "20:00-21:30"
                user_id INTEGER NOT NULL,
                discord_name TEXT,
                role_type TEXT DEFAULT 'private',
                present BOOLEAN DEFAULT 0,
                excused TEXT DEFAULT NULL,  -- 'У/П' или NULL
                reason TEXT DEFAULT NULL,
                vc_time_seconds INTEGER DEFAULT 0,
                processed_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, date, user_id, schedule_id)
            );
            
            -- ========================================
            -- ЛОГИ ДЕЙСТВИЙ
            -- ========================================

            CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                actor_id INTEGER,
                target_id INTEGER,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Индексы
            CREATE INDEX IF NOT EXISTS idx_voice_sessions_guild_date ON voice_sessions(guild_id, date);
            CREATE INDEX IF NOT EXISTS idx_kv_attendance_guild_date ON kv_attendance(guild_id, date);
        ''')
        await self.db.commit()

        # Миграция старых БД: колонка sunday_default могла отсутствовать
        try:
            await self.db.execute('ALTER TABLE guild_settings ADD COLUMN sunday_default TEXT DEFAULT NULL')
            await self.db.commit()
        except Exception:
            pass  # колонка уже существует

        logger.info("✅ База данных v3.0 инициализирована")

        await self.load_guild_cache()
    
    async def load_guild_cache(self):
        """Загружает настройки в кэш"""
        # Настройки
        async with self.db.execute('''
            SELECT guild_id, kv_vc_channel_id,
                   report_channel_id, log_channel_id, sunday_default
            FROM guild_settings
        ''') as cursor:
            async for row in cursor:
                self.guild_settings[row[0]] = {
                    'kv_vc_channel_id': row[1],
                    'report_channel_id': row[2],
                    'log_channel_id': row[3],
                    'sunday_default': row[4]
                }
        
        # Выбор воскресного события (Потасовка / Захват базы)
        async with self.db.execute('SELECT guild_id, date, event_name FROM kv_event_choice') as cursor:
            async for row in cursor:
                self.sunday_choices[f"{row[0]}:{row[1]}"] = row[2]

        # Ручные звания участников
        async with self.db.execute('SELECT guild_id, user_id, rank FROM member_ranks') as cursor:
            async for row in cursor:
                self.member_ranks[f"{row[0]}:{row[1]}"] = row[2]

        # Роли
        async with self.db.execute('SELECT * FROM guild_roles') as cursor:
            async for row in cursor:
                guild_id = row[1]
                role_type = row[2]
                role_id = row[3]
                
                if guild_id not in self.guild_roles:
                    self.guild_roles[guild_id] = {}
                if role_type not in self.guild_roles[guild_id]:
                    self.guild_roles[guild_id][role_type] = []
                self.guild_roles[guild_id][role_type].append(role_id)
        
        logger.info(f"📦 Загружен кэш: {len(self.guild_settings)} гильдий")
    
    def get_guild_schedules(self, guild_id: int) -> List[Dict]:
        """Возвращает фиксированное расписание КВ (одинаково для всех серверов)."""
        return FIXED_SCHEDULE

    def get_event_stages(self, event_name: str) -> List:
        """Этапы события: список (начало, конец). Пусто, если этапы не заданы."""
        return EVENT_STAGES.get(event_name, [])

    def get_sunday_choice(self, guild_id: int, date_str: str) -> Optional[str]:
        """Выбранное событие на конкретное воскресенье: точечный выбор → выбор по умолчанию → None."""
        choice = self.sunday_choices.get(f"{guild_id}:{date_str}")
        if choice:
            return choice
        return self.guild_settings.get(guild_id, {}).get('sunday_default')

    def get_event_name(self, guild_id: int, date_str: str, schedule: Dict) -> str:
        """Имя события с учётом воскресного выбора (Потасовка / Захват базы)."""
        if schedule.get('choices'):
            choice = self.get_sunday_choice(guild_id, date_str)
            if choice:
                return choice
        return schedule['name']

    async def set_sunday_choice(self, guild_id: int, date_str: str, event_name: str, chosen_by: int):
        """Сохраняет точечный выбор воскресного события на дату."""
        await self.db.execute('''
            INSERT INTO kv_event_choice (guild_id, date, event_name, chosen_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, date) DO UPDATE SET
                event_name = excluded.event_name, chosen_by = excluded.chosen_by
        ''', (guild_id, date_str, event_name, chosen_by))
        await self.db.commit()
        self.sunday_choices[f"{guild_id}:{date_str}"] = event_name

    async def set_member_rank(self, guild_id: int, user_id: int, rank: str, set_by: int):
        """Назначает звание участнику вручную (приоритетнее ролей Discord)."""
        await self.db.execute('''
            INSERT INTO member_ranks (guild_id, user_id, rank, set_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                rank = excluded.rank, set_by = excluded.set_by, created_at = CURRENT_TIMESTAMP
        ''', (guild_id, user_id, rank, set_by))
        await self.db.commit()
        self.member_ranks[f"{guild_id}:{user_id}"] = rank

    async def clear_member_rank(self, guild_id: int, user_id: int) -> bool:
        """Убирает ручное звание (вернётся определение по ролям Discord)."""
        result = await self.db.execute(
            'DELETE FROM member_ranks WHERE guild_id = ? AND user_id = ?',
            (guild_id, user_id)
        )
        await self.db.commit()
        self.member_ranks.pop(f"{guild_id}:{user_id}", None)
        return result.rowcount > 0

    async def set_sunday_default(self, guild_id: int, event_name: Optional[str]):
        """Постоянный выбор по умолчанию для всех воскресений (None — сбросить)."""
        await self.db.execute('''
            INSERT INTO guild_settings (guild_id, sunday_default)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                sunday_default = excluded.sunday_default, updated_at = CURRENT_TIMESTAMP
        ''', (guild_id, event_name))
        await self.db.commit()
        self.guild_settings.setdefault(guild_id, {})['sunday_default'] = event_name

    def get_current_kv_schedule(self, guild_id: int) -> Optional[Dict]:
        """Проверяет, идёт ли сейчас КВ (по фиксированному расписанию)"""
        now = datetime.now(self.timezone)
        current_day = now.weekday()
        current_time = now.time()

        for schedule in FIXED_SCHEDULE:
            if current_day not in schedule['days_of_week']:
                continue

            start = datetime.strptime(schedule['start_time'], '%H:%M').time()
            end = datetime.strptime(schedule['end_time'], '%H:%M').time()

            if start <= end:
                if start <= current_time <= end:
                    return schedule
            else:
                if current_time >= start or current_time <= end:
                    return schedule

        return None

    def get_member_role_type(self, member: discord.Member) -> str:
        """
        Определяет звание участника по иерархии.
        Приоритет: ручное назначение в боте → роль Discord → 'private' (Рядовой).
        """
        guild_id = member.guild.id

        # 1) Ручное звание, назначенное в боте
        override = self.member_ranks.get(f"{guild_id}:{member.id}")
        if override in RANK_ORDER:
            return override

        # 2) По ролям Discord (от высшего звания к низшему)
        guild_roles = self.guild_roles.get(guild_id, {})
        if guild_roles:
            user_role_ids = [role.id for role in member.roles]
            for rank in RANK_ORDER:
                for role_id in guild_roles.get(rank, []):
                    if role_id in user_role_ids:
                        return rank

        # 3) По умолчанию
        return DEFAULT_RANK

    async def has_permission(self, member: discord.Member, permission_level: str) -> bool:
        """Проверяет, что звание участника не ниже требуемого."""
        if member.guild_permissions.administrator:
            return True

        if permission_level not in RANK_ORDER:
            return False

        rank = self.get_member_role_type(member)
        # Меньший индекс в RANK_ORDER = более высокое звание
        return RANK_ORDER.index(rank) <= RANK_ORDER.index(permission_level)
    
    async def send_log(self, guild: discord.Guild, embed: discord.Embed):
        """Отправляет лог в канал логов"""
        settings = self.guild_settings.get(guild.id, {})
        log_channel_id = settings.get('log_channel_id')
        
        if log_channel_id:
            channel = guild.get_channel(log_channel_id)
            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    logger.error(f"Ошибка отправки лога: {e}")
    
    async def on_ready(self):
        """Событие готовности"""
        logger.info(f"{'='*50}")
        logger.info(f"🎮 Бот запущен: {self.user.name} ({self.user.id})")
        logger.info(f"📡 Подключён к {len(self.guilds)} серверам")
        logger.info(f"{'='*50}")
        
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="за кланом | !help"
            )
        )
        
        if not self.schedule_checker.is_running():
            self.schedule_checker.start()
        
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ Синхронизировано {len(synced)} slash-команд")
        except Exception as e:
            logger.error(f"❌ Ошибка синхронизации: {e}")
    
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """
        Трекинг ВСЕХ голосовых каналов
        Записывает каждый вход/выход в любой VC
        """
        if member.bot:
            return
        
        guild_id = member.guild.id
        now = datetime.now(self.timezone)
        today = date_for_db(now)
        session_key = f"{guild_id}:{member.id}"
        
        settings = self.guild_settings.get(guild_id, {})
        kv_vc_id = settings.get('kv_vc_channel_id')
        
        # ВАЖНО: сначала обрабатываем ВЫХОД, потом ВХОД.
        # При переходе между каналами Discord присылает before и after одновременно,
        # и оба блока истинны. Если обработать ВХОД первым, он создаст новую сессию,
        # которую блок ВЫХОД тут же закроет с нулевой длительностью.

        # ===== ВЫХОД ИЗ ГОЛОСОВОГО КАНАЛА =====
        if before.channel and (after.channel is None or before.channel.id != after.channel.id):

            if session_key in self.all_voice_sessions:
                session = self.all_voice_sessions.pop(session_key)
                duration = (now - session['join_time']).total_seconds()

                await self.db.execute('''
                    UPDATE voice_sessions SET leave_time = ?, duration_seconds = ?, status = 'completed'
                    WHERE guild_id = ? AND user_id = ? AND status = 'active'
                ''', (now.isoformat(), int(duration), guild_id, member.id))
                await self.db.commit()

                logger.info(f"🎤 ВЫХОД | {member.display_name} ← {before.channel.name} | {format_duration(int(duration))}")

                # Лог
                log_embed = discord.Embed(
                    title="🎤 Выход из голосового канала",
                    description=f"**{member.display_name}** вышел из **{before.channel.name}**\n"
                               f"⏱️ Время: **{format_duration(int(duration))}**",
                    color=discord.Color.orange(),
                    timestamp=now
                )
                await self.send_log(member.guild, log_embed)

                # Обновляем время в КВ
                if kv_vc_id and before.channel.id == kv_vc_id:
                    await self.db.execute('''
                        UPDATE kv_attendance SET vc_time_seconds = vc_time_seconds + ?
                        WHERE guild_id = ? AND date = ? AND user_id = ?
                    ''', (int(duration), guild_id, today, member.id))
                    await self.db.commit()

        # ===== ВХОД В ЛЮБОЙ ГОЛОСОВОЙ КАНАЛ =====
        if after.channel and (before.channel is None or before.channel.id != after.channel.id):

            # Страховка: закрываем «повисшую» сессию, если она осталась
            # (например, после рестарта бота при активном статусе в БД)
            if session_key in self.all_voice_sessions:
                old_session = self.all_voice_sessions.pop(session_key)
                duration = (now - old_session['join_time']).total_seconds()

                await self.db.execute('''
                    UPDATE voice_sessions SET leave_time = ?, duration_seconds = ?, status = 'completed'
                    WHERE guild_id = ? AND user_id = ? AND status = 'active'
                ''', (now.isoformat(), int(duration), guild_id, member.id))

            # Создаём новую сессию
            self.all_voice_sessions[session_key] = {
                'join_time': now,
                'channel_id': after.channel.id,
                'channel_name': after.channel.name,
                'guild_id': guild_id
            }

            await self.db.execute('''
                INSERT INTO voice_sessions
                (guild_id, user_id, username, display_name, channel_id, channel_name, join_time, date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
            ''', (
                guild_id, member.id, str(member), member.display_name,
                after.channel.id, after.channel.name, now.isoformat(), today
            ))
            await self.db.commit()

            logger.info(f"🎤 ВХОД | {member.display_name} → {after.channel.name}")

            # Лог в канал
            log_embed = discord.Embed(
                title="🎤 Вход в голосовой канал",
                description=f"**{member.display_name}** зашёл в **{after.channel.name}**",
                color=discord.Color.green(),
                timestamp=now
            )
            log_embed.set_thumbnail(url=member.display_avatar.url)
            await self.send_log(member.guild, log_embed)

            # Проверяем КВ
            if kv_vc_id and after.channel.id == kv_vc_id:
                current_kv = self.get_current_kv_schedule(guild_id)
                if current_kv:
                    role_type = self.get_member_role_type(member)
                    await self.db.execute('''
                        INSERT INTO kv_attendance
                        (guild_id, schedule_id, date, kv_time, user_id, discord_name, role_type, present)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(guild_id, date, user_id, schedule_id) DO UPDATE SET
                            present = 1, role_type = excluded.role_type
                    ''', (
                        guild_id, current_kv['id'], today,
                        f"{current_kv['start_time']}-{current_kv['end_time']}",
                        member.id, member.display_name, role_type
                    ))
                    await self.db.commit()

    @tasks.loop(minutes=1)
    async def schedule_checker(self):
        """Проверка расписания КВ и уведомления"""
        now = datetime.now(self.timezone)
        current_day = now.weekday()
        today = date_for_db(now)

        for guild in self.guilds:
            guild_id = guild.id

            settings = self.guild_settings.get(guild_id, {})
            report_channel_id = settings.get('report_channel_id')

            for schedule in FIXED_SCHEDULE:
                if current_day not in schedule['days_of_week']:
                    continue

                event_name = self.get_event_name(guild_id, today, schedule)
                start_time = datetime.strptime(schedule['start_time'], '%H:%M').time()
                end_time = datetime.strptime(schedule['end_time'], '%H:%M').time()
                notify_before = schedule.get('notify_before', 15)

                # Уведомление о начале КВ
                notify_time = (datetime.combine(now.date(), start_time) - timedelta(minutes=notify_before)).time()

                if now.hour == notify_time.hour and now.minute == notify_time.minute:
                    if report_channel_id:
                        channel = guild.get_channel(report_channel_id)
                        if channel:
                            embed = discord.Embed(
                                title=f"⚔️ КВ через {notify_before} минут!",
                                description=f"**{event_name}**\n"
                                           f"🕐 Время: **{schedule['start_time']} - {schedule['end_time']}**",
                                color=discord.Color.red(),
                                timestamp=now
                            )
                            
                            # Упоминаем роли
                            mentions = []
                            if guild_id in self.guild_roles:
                                for role_type in RANK_ORDER:
                                    if role_type in self.guild_roles[guild_id]:
                                        for role_id in self.guild_roles[guild_id][role_type]:
                                            role = guild.get_role(role_id)
                                            if role:
                                                mentions.append(role.mention)
                            
                            try:
                                await channel.send(content=" ".join(mentions) if mentions else None, embed=embed)
                            except Exception as e:
                                logger.error(f"Ошибка уведомления КВ: {e}")
                
                # Уведомление о завершении КВ
                if now.hour == end_time.hour and now.minute == end_time.minute:
                    if report_channel_id:
                        channel = guild.get_channel(report_channel_id)
                        if channel:
                            # Статистика КВ
                            async with self.db.execute('''
                                SELECT COUNT(*), SUM(vc_time_seconds)
                                FROM kv_attendance
                                WHERE guild_id = ? AND date = ? AND schedule_id = ? AND present = 1
                            ''', (guild_id, today, schedule['id'])) as cursor:
                                stats = await cursor.fetchone()
                            
                            present = stats[0] or 0
                            total_time = stats[1] or 0
                            avg_time = int(total_time / present / 60) if present > 0 else 0
                            
                            embed = discord.Embed(
                                title="🏁 КВ завершена!",
                                description=f"**{event_name}**",
                                color=discord.Color.green(),
                                timestamp=now
                            )
                            embed.add_field(name="👥 Участников", value=str(present), inline=True)
                            embed.add_field(name="⏱️ Среднее время", value=f"{avg_time} мин", inline=True)
                            embed.add_field(name="📅 Дата", value=format_date(now), inline=True)
                            embed.set_footer(text="Используйте !kv для полного отчёта")
                            
                            try:
                                await channel.send(embed=embed)
                            except Exception as e:
                                logger.error(f"Ошибка уведомления завершения КВ: {e}")
    
    @schedule_checker.before_loop
    async def before_schedule_checker(self):
        await self.wait_until_ready()
    
    async def close(self):
        """Корректное закрытие"""
        logger.info("🛑 Завершение работы...")
        
        now = datetime.now(self.timezone)
        for session_key, session in self.all_voice_sessions.items():
            try:
                duration = (now - session['join_time']).total_seconds()
                await self.db.execute('''
                    UPDATE voice_sessions 
                    SET leave_time = ?, duration_seconds = ?, status = 'interrupted'
                    WHERE guild_id = ? AND user_id = ? AND status = 'active'
                ''', (now.isoformat(), int(duration), session['guild_id'], int(session_key.split(':')[1])))
            except Exception as e:
                logger.error(f"Ошибка при закрытии голосовой сессии {session_key}: {e}")

        if self.db:
            await self.db.commit()
            await self.db.close()
        
        await super().close()


# ============================================
# КОМАНДА HELP
# ============================================

@commands.command(name='help', aliases=['помощь', 'h'])
async def help_command(ctx: commands.Context):
    """Показывает справку по командам"""
    
    embed = discord.Embed(
        title="📖 StalZone Clan Bot v3.2",
        description="Полный список команд",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="⚙️ Настройка (Админ)",
        value="`!setup` - Мастер настройки\n"
              "`!setvc #канал` - VC для КВ\n"
              "`!setchannel log #канал` - Канал логов\n"
              "`!setchannel report #канал` - Канал отчётов",
        inline=False
    )
    
    embed.add_field(
        name="🪖 Звания (Админ)",
        value="Иерархия: Лидер › Полковник › Офицер › Сержант › Боец › Рядовой\n"
              "`!setleader @роль` `!setcolonel @роль` `!setofficer @роль`\n"
              "`!setsergeant @роль` `!setfighter @роль` `!setprivate @роль`\n"
              "`!setrole <звание> @роль` - то же одной командой\n"
              "`!setrank @игрок <звание>` - Звание вручную\n"
              "`!rank [@игрок]` / `!roles` - Показать звание / иерархию",
        inline=False
    )
    
    embed.add_field(
        name="⚔️ КВ - Клановые войны",
        value="`!schedule` - Фиксированное расписание (20:00-21:00)\n"
              "`!calendar` - Календарь КВ на месяц (картинкой)\n"
              "`!kv` - Кто сейчас в VC (только во время КВ!)\n"
              "`!kv 17-01-2026` - Отчёт за конкретную дату\n"
              "`!kvedit` - Редактировать посещаемость (меню)\n"
              "`!kvedit 17-01-2026 @user присутствовал`\n"
              "_Вс: выбор Потасовка / Захват базы — кнопками в `!kv` или `!calendar`_",
        inline=False
    )
    
    embed.add_field(
        name="📊 Статистика",
        value="`!me` - Своя статистика по КВ\n"
              "`!me @user` - Статистика другого (офицеры+)\n"
              "`!stats @user` - То же что !me\n"
              "`!top10` - Топ-10 по КВ\n"
              "`!online` - Кто сейчас в голосовых\n"
              "`!export` - Экспорт в Excel (2 листа)",
        inline=False
    )
    
    embed.set_footer(text="StalZone Clan Bot v3.2 | Все даты в формате DD-MM-YYYY")
    await ctx.send(embed=embed)


# ============================================
# ЗАПУСК
# ============================================

async def main():
    bot = ClanBot()
    bot.add_command(help_command)
    
    try:
        async with bot:
            await bot.start(config['TOKEN'])
    except discord.LoginFailure:
        logger.critical("❌ Неверный токен!")
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
