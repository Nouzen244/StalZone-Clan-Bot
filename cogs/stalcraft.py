"""
Модуль StalZone: календарь КВ (картинкой) и сервисные команды.
"""

import discord
from discord.ext import commands
import logging
import io
import calendar as pycalendar
from datetime import datetime, timedelta

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

logger = logging.getLogger('StalZone')


# Цвета кружков по типу события КВ
KV_DOT_COLORS = {
    'Потасовка': (59, 130, 246),    # синий
    'Турнир': (239, 68, 68),        # красный
    'Захват базы': (245, 197, 24),  # жёлтый
}

RU_MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
             'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']


def _load_font(size: int, bold: bool = False):
    """Подбирает системный шрифт с поддержкой кириллицы."""
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


# ============================================
# КОГ
# ============================================

class StalcraftCog(commands.Cog, name="StalZone"):
    """Календарь КВ и сервисные команды"""

    def __init__(self, bot):
        self.bot = bot
        logger.info("🎮 StalcraftCog загружен")

    @commands.command(name='calendar', aliases=['календарь'])
    async def calendar(self, ctx: commands.Context):
        """Календарь КВ на текущий месяц (картинкой)"""
        if not PIL_AVAILABLE:
            await ctx.send("❌ Pillow не установлен! `pip install Pillow`")
            return

        now = datetime.now(self.bot.timezone)
        buf = self._render_calendar(ctx.guild.id, now.year, now.month)
        file = discord.File(buf, filename="calendar.png")
        view = CalendarView(self, self.bot, ctx.guild.id)
        await ctx.send(
            content="🔵 Потасовка · 🔴 Турнир · 🟡 Захват базы\n"
                    "_Воскресенье: выбор события — кнопками ниже (офицеры+)_",
            file=file, view=view
        )

    def _sunday_dots(self, guild_id: int, date_str: str):
        """Цвета кружков для воскресенья: выбранное событие → один, иначе оба."""
        choice = self.bot.get_sunday_choice(guild_id, date_str)
        if choice == 'Потасовка':
            return [KV_DOT_COLORS['Потасовка']]
        if choice == 'Захват базы':
            return [KV_DOT_COLORS['Захват базы']]
        return [KV_DOT_COLORS['Потасовка'], KV_DOT_COLORS['Захват базы']]

    def _render_calendar(self, guild_id: int, year: int, month: int) -> io.BytesIO:
        """Рисует календарь месяца с цветными кружками КВ под датами."""
        margin = 28
        title_h = 64
        dow_h = 38
        cell_w = 60
        cell_h = 64
        cols = 7
        weeks = pycalendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
        rows = len(weeks)

        width = margin * 2 + cell_w * cols
        height = margin + title_h + dow_h + cell_h * rows + margin

        img = Image.new("RGB", (width, height), (24, 25, 28))
        draw = ImageDraw.Draw(img)

        f_title = _load_font(26, bold=True)
        f_year = _load_font(20, bold=False)
        f_dow = _load_font(17, bold=True)
        f_day = _load_font(20, bold=False)

        # Заголовок: месяц в плашке + год
        mname = RU_MONTHS[month - 1].upper()
        pad_x, pad_y = 12, 6
        tb = draw.textbbox((0, 0), mname, font=f_title)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        box = (margin, margin, margin + tw + pad_x * 2, margin + th + pad_y * 2 + 6)
        draw.rounded_rectangle(box, radius=10, fill=(48, 50, 56))
        draw.text((margin + pad_x, margin + pad_y), mname, font=f_title, fill=(235, 235, 235))
        draw.text((box[2] + 12, margin + pad_y + 2), str(year), font=f_year, fill=(150, 150, 155))

        # Дни недели
        dow = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        dow_y = margin + title_h
        for i, d in enumerate(dow):
            cx = margin + i * cell_w + cell_w // 2
            db = draw.textbbox((0, 0), d, font=f_dow)
            draw.text((cx - (db[2] - db[0]) // 2, dow_y), d, font=f_dow,
                      fill=(170, 170, 175) if i < 5 else (150, 150, 162))

        now = datetime.now(self.bot.timezone)
        today = now.day if (now.year == year and now.month == month) else -1

        grid_top = margin + title_h + dow_h
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                if day == 0:
                    continue
                cx = margin + c * cell_w + cell_w // 2
                cy = grid_top + r * cell_h
                date_str = f"{year:04d}-{month:02d}-{day:02d}"
                weekday = c  # firstweekday=0 → c == weekday()

                # Подсветка сегодня
                if day == today:
                    draw.rounded_rectangle((cx - 18, cy + 2, cx + 18, cy + 30),
                                           radius=8, fill=(48, 50, 56))

                num = str(day)
                nb = draw.textbbox((0, 0), num, font=f_day)
                num_color = (245, 245, 245) if day == today else (212, 212, 216)
                draw.text((cx - (nb[2] - nb[0]) // 2, cy + 6), num, font=f_day, fill=num_color)

                # Кружки события
                if weekday in (0, 1, 2):
                    dots = [KV_DOT_COLORS['Потасовка']]
                elif weekday in (3, 4, 5):
                    dots = [KV_DOT_COLORS['Турнир']]
                else:
                    dots = self._sunday_dots(guild_id, date_str)

                rad, gap = 5, 6
                total_w = len(dots) * (rad * 2) + (len(dots) - 1) * gap
                start_x = cx - total_w // 2 + rad
                dy = cy + 42
                for k, color in enumerate(dots):
                    dxc = start_x + k * (rad * 2 + gap)
                    draw.ellipse((dxc - rad, dy - rad, dxc + rad, dy + rad), fill=color)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    @commands.command(name='ping')
    async def ping(self, ctx: commands.Context):
        """Проверка бота"""
        latency = round(self.bot.latency * 1000)
        color = discord.Color.green() if latency < 100 else discord.Color.yellow() if latency < 200 else discord.Color.red()
        embed = discord.Embed(title="🏓 Pong!", color=color)
        embed.add_field(name="Задержка", value=f"{latency}ms")
        await ctx.send(embed=embed)

    @commands.command(name='uptime')
    async def uptime(self, ctx: commands.Context):
        """Время работы"""
        uptime = datetime.now() - self.bot.start_time
        embed = discord.Embed(
            title="⏱️ Время работы",
            description=f"**{uptime.days}** дн. **{uptime.seconds // 3600}** ч. **{(uptime.seconds % 3600) // 60}** мин.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)


class CalendarView(discord.ui.View):
    """Кнопки выбора воскресного события для !calendar (офицеры+).

    Календарь всегда показывает только текущий месяц, поэтому месяц/год
    вычисляются на лету, а выбор воскресенья ограничен текущим месяцем.
    """

    def __init__(self, cog, bot, guild_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.bot = bot
        self.guild_id = guild_id
        self.last_choice = None

    async def _is_officer(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if await self.bot.has_permission(interaction.user, 'officer'):
            return True
        await interaction.response.send_message(
            "🚫 Только офицеры могут выбирать воскресное событие!", ephemeral=True
        )
        return False

    def _this_month_sunday(self):
        """Ближайшее предстоящее воскресенье в текущем месяце (или None)."""
        now = datetime.now(self.bot.timezone)
        target = now + timedelta(days=(6 - now.weekday()) % 7)
        if target.year != now.year or target.month != now.month:
            return None
        return target.strftime('%Y-%m-%d')

    async def _refresh(self, interaction: discord.Interaction):
        now = datetime.now(self.bot.timezone)
        buf = self.cog._render_calendar(self.guild_id, now.year, now.month)
        file = discord.File(buf, filename="calendar.png")
        await interaction.response.edit_message(attachments=[file], view=self)

    async def _pick(self, interaction: discord.Interaction, event: str):
        if not await self._is_officer(interaction):
            return
        self.last_choice = event
        date_str = self._this_month_sunday()
        if date_str:
            await self.bot.set_sunday_choice(self.guild_id, date_str, event, interaction.user.id)
            await self._refresh(interaction)
        else:
            await interaction.response.send_message(
                f"В этом месяце предстоящих воскресений больше нет.\n"
                f"Нажмите «📌 Запомнить мой выбор», чтобы сделать **{event}** постоянным.",
                ephemeral=True
            )

    @discord.ui.button(label="Потасовка", emoji="🔵", style=discord.ButtonStyle.primary)
    async def pick_skirmish(self, interaction: discord.Interaction, button):
        await self._pick(interaction, 'Потасовка')

    @discord.ui.button(label="Захват базы", emoji="🟡", style=discord.ButtonStyle.primary)
    async def pick_capture(self, interaction: discord.Interaction, button):
        await self._pick(interaction, 'Захват базы')

    @discord.ui.button(label="📌 Запомнить мой выбор", style=discord.ButtonStyle.success, row=1)
    async def remember(self, interaction: discord.Interaction, button):
        if not await self._is_officer(interaction):
            return
        if not self.last_choice:
            await interaction.response.send_message(
                "Сначала выберите событие: 🔵 Потасовка или 🟡 Захват базы.", ephemeral=True
            )
            return
        await self.bot.set_sunday_default(self.guild_id, self.last_choice)
        await self._refresh(interaction)

    @discord.ui.button(label="♻️ Сбросить", style=discord.ButtonStyle.secondary, row=1)
    async def reset_default(self, interaction: discord.Interaction, button):
        if not await self._is_officer(interaction):
            return
        await self.bot.set_sunday_default(self.guild_id, None)
        await self._refresh(interaction)


class ErrorHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("🚫 Недостаточно прав!", delete_after=10)
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("🚫 Нет доступа!", delete_after=10)
        elif isinstance(error, commands.CommandNotFound):
            pass
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Не хватает: `{error.param.name}`", delete_after=10)
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Неверный аргумент", delete_after=10)
        else:
            logger.error(f"Ошибка: {error}")
            await ctx.send(f"❌ Ошибка: {error}", delete_after=15)


async def setup(bot):
    await bot.add_cog(StalcraftCog(bot))
    await bot.add_cog(ErrorHandler(bot))
