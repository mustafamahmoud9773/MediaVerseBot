
import os
import html
import asyncio
from datetime import datetime, timezone

import aiohttp
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TMDB_TOKEN = os.getenv("TMDB_TOKEN", "").strip()
RAWG_KEY = os.getenv("RAWG_KEY", "").strip()

TMDB_BASE = "https://api.themoviedb.org/3"
RAWG_BASE = "https://api.rawg.io/api"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")

HEADERS = {
    "accept": "application/json",
    "User-Agent": "MediaVerseBot/1.0"
}

async def http_get(url, headers=None, params=None):
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as s:
        async with s.get(url, headers=headers, params=params) as r:
            if r.status != 200:
                return None
            return await r.json()

async def tmdb(path, params=None):
    if not TMDB_TOKEN:
        return None
    h = {"Authorization": f"Bearer {TMDB_TOKEN}", "accept": "application/json"}
    return await http_get(TMDB_BASE + path, headers=h, params=params or {})

async def rawg(path, params=None):
    if not RAWG_KEY:
        return None
    p = dict(params or {})
    p["key"] = RAWG_KEY
    return await http_get(RAWG_BASE + path, params=p)

def esc(x):
    return html.escape(str(x or ""))

def cut(text, n=900):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n-1] + "…"

def date_status(date_text, tba=False):
    if tba or not date_text:
        return "🟡 لم يُحدد موعد إصدار رسمي"
    try:
        d = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if d.date() > now.date():
            return f"🟡 لم تصدر بعد — الموعد: {date_text}"
        return f"🟢 صدرت — {date_text}"
    except Exception:
        return f"📅 {date_text}"

async def movie_search(q):
    data = await tmdb("/search/multi", {"query": q, "language": "en-US", "include_adult": "false"})
    if not data:
        return []
    return [
        x for x in data.get("results", [])
        if x.get("media_type") in ("movie", "tv")
    ][:8]

async def game_search(q):
    data = await rawg("/games", {"search": q, "page_size": 8, "search_precise": "true"})
    return (data or {}).get("results", [])

async def search_all(q):
    movies = await movie_search(q)
    games = await game_search(q) if RAWG_KEY else []
    return movies, games

def result_keyboard(movies, games):
    rows = []
    for x in movies:
        typ = x["media_type"]
        title = x.get("title") or x.get("name") or "Unknown"
        label = f"🎬 {title}"[:60]
        rows.append([InlineKeyboardButton(label, callback_data=f"m:{typ}:{x['id']}")])
    for x in games:
        rows.append([InlineKeyboardButton(f"🎮 {x.get('name','Unknown')}"[:60],
                                          callback_data=f"g:{x['id']}")])
    return InlineKeyboardMarkup(rows[:18])

async def send_movie(update, typ, item_id):
    data = await tmdb(
        f"/{typ}/{item_id}",
        {"language": "en-US", "append_to_response": "credits,release_dates,external_ids"}
    )
    if not data:
        await update.effective_message.reply_text("❌ تعذر جلب بيانات العمل.")
        return

    title = data.get("title") or data.get("name") or data.get("original_title") or data.get("original_name")
    release = data.get("release_date") or data.get("first_air_date")
    runtime = data.get("runtime")
    if not runtime and data.get("episode_run_time"):
        runtime = data["episode_run_time"][0] if data["episode_run_time"] else None

    companies = ", ".join(c["name"] for c in data.get("production_companies", [])[:8])
    genres = ", ".join(g["name"] for g in data.get("genres", [])[:8])
    directors = []
    writers = []
    for p in data.get("credits", {}).get("crew", []):
        if p.get("job") == "Director":
            directors.append(p.get("name"))
        if p.get("department") == "Writing" and p.get("name"):
            writers.append(p.get("name"))
    cast = data.get("credits", {}).get("cast", [])

    text = (
        f"🎬 <b>{esc(title)}</b>\n\n"
        f"📅 <b>الإصدار:</b> {esc(date_status(release))}\n"
        f"⭐ <b>التقييم:</b> {data.get('vote_average', '—')}\n"
        f"🎭 <b>النوع:</b> {esc(genres) or '—'}\n"
        f"⏱️ <b>المدة:</b> {runtime or '—'} دقيقة\n"
        f"🎥 <b>المخرج:</b> {esc(', '.join(dict.fromkeys(directors)) or '—')}\n"
        f"✍️ <b>الكتابة:</b> {esc(', '.join(dict.fromkeys(writers)) or '—')}\n"
        f"🏢 <b>شركات الإنتاج:</b> {esc(companies or '—')}\n\n"
        f"📝 <b>نبذة:</b>\n{esc(cut(data.get('overview'), 1100)) or 'لا توجد نبذة.'}\n\n"
        f"👥 <b>أبرز الشخصيات/طاقم التمثيل:</b>\n"
    )
    for c in cast[:15]:
        character = c.get("character") or "—"
        text += f"• {esc(c.get('name'))} — {esc(character)}\n"

    text += "\n🔎 المصدر: TMDB"

    buttons = [
        [InlineKeyboardButton("👥 كل الشخصيات", callback_data=f"cast:{typ}:{item_id}:0")],
        [InlineKeyboardButton("📰 الأخبار", callback_data=f"news:{typ}:{item_id}")],
    ]
    poster = data.get("poster_path")
    if poster:
        try:
            await update.effective_message.reply_photo(
                photo=TMDB_IMAGE + poster,
                caption=text[:1024],
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return
        except Exception:
            pass
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
    )

async def send_cast(query, typ, item_id, page=0):
    data = await tmdb(f"/{typ}/{item_id}/credits", {"language": "en-US"})
    if not data:
        await query.message.reply_text("❌ تعذر جلب الشخصيات.")
        return
    cast = data.get("cast", [])
    start = page * 20
    chunk = cast[start:start+20]
    if not chunk:
        await query.message.reply_text("لا توجد شخصيات إضافية.")
        return
    title = "👥 <b>الشخصيات / طاقم التمثيل</b>\n\n"
    for i, c in enumerate(chunk, start=start+1):
        title += f"{i}. <b>{esc(c.get('character') or '—')}</b> — {esc(c.get('name') or '—')}\n"
    title += f"\n📄 صفحة {page+1}"
    buttons = []
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"cast:{typ}:{item_id}:{page-1}"))
    if start + 20 < len(cast):
        nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"cast:{typ}:{item_id}:{page+1}"))
    if nav:
        buttons.append(nav)
    await query.message.reply_text(title, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

async def send_game(update, game_id):
    data = await rawg(f"/games/{game_id}")
    if not data:
        await update.effective_message.reply_text("❌ تعذر جلب بيانات اللعبة.")
        return

    developers = ", ".join(x.get("name","") for x in data.get("developers", []))
    publishers = ", ".join(x.get("name","") for x in data.get("publishers", []))
    genres = ", ".join(x.get("name","") for x in data.get("genres", []))
    platforms = ", ".join(x.get("platform", {}).get("name","") for x in data.get("platforms", []))
    creators = ", ".join(x.get("name","") for x in data.get("creators", [])[:15])

    text = (
        f"🎮 <b>{esc(data.get('name'))}</b>\n\n"
        f"📅 <b>الإصدار:</b> {esc(date_status(data.get('released'), data.get('tba', False)))}\n"
        f"🏢 <b>المطور:</b> {esc(developers or '—')}\n"
        f"📦 <b>الناشر:</b> {esc(publishers or '—')}\n"
        f"🎭 <b>النوع:</b> {esc(genres or '—')}\n"
        f"🕹️ <b>المنصات:</b> {esc(platforms or '—')}\n"
        f"⭐ <b>التقييم:</b> {data.get('rating', '—')}\n"
        f"🎯 <b>Metacritic:</b> {data.get('metacritic', '—')}\n"
        f"👨‍💻 <b>أعضاء فريق التطوير:</b> {esc(creators or '—')}\n\n"
        f"📝 <b>الوصف:</b>\n{esc(cut(data.get('description_raw') or data.get('description'), 1200))}\n\n"
        f"🔎 المصدر: RAWG"
    )
    buttons = [
        [InlineKeyboardButton("👨‍💻 فريق التطوير", callback_data=f"devteam:{game_id}")],
        [InlineKeyboardButton("📰 الأخبار", callback_data=f"gnews:{game_id}")],
    ]
    image = data.get("background_image")
    if image:
        try:
            await update.effective_message.reply_photo(
                photo=image, caption=text[:1024], parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return
        except Exception:
            pass
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌑 <b>MediaVerse</b>\n\n"
        "ابعت اسم فيلم، مسلسل أو لعبة وسأبحث عنها.\n\n"
        "أمثلة:\n"
        "• <code>Interstellar</code>\n"
        "• <code>GTA VI</code>\n"
        "• <code>The Witcher 4</code>\n\n"
        "أستخدم مصادر بيانات متخصصة، وأميز بين موعد الإصدار المؤكد وعدم تحديد الموعد.",
        parse_mode=ParseMode.HTML
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔎 فقط أرسل اسم العمل.\n"
        "/start — البداية\n"
        "/help — المساعدة"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip()
    if len(q) < 2:
        return
    await update.message.reply_text("🔎 بدور على النتائج…")
    movies, games = await search_all(q)
    if not movies and not games:
        await update.message.reply_text(
            "❌ ملقيتش نتيجة واضحة.\nجرّب الاسم بالإنجليزية أو الاسم الرسمي."
        )
        return
    await update.message.reply_text(
        "اختر العمل المطلوب:",
        reply_markup=result_keyboard(movies, games)
    )

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split(":")
    kind = data[0]

    if kind == "m":
        await send_movie(update, data[1], int(data[2]))
    elif kind == "g":
        await send_game(update, int(data[1]))
    elif kind == "cast":
        await send_cast(q, data[1], int(data[2]), int(data[3]))
    elif kind == "devteam":
        gid = int(data[1])
        d = await rawg(f"/games/{gid}/development-team", {"page_size": 40})
        names = [x.get("name") for x in (d or {}).get("results", []) if x.get("name")]
        if not names:
            await q.message.reply_text("لا توجد بيانات متاحة عن فريق التطوير.")
        else:
            await q.message.reply_text("👨‍💻 <b>فريق التطوير</b>\n\n" +
                                        "\n".join(f"• {esc(n)}" for n in names),
                                        parse_mode=ParseMode.HTML)
    elif kind in ("news", "gnews"):
        await q.message.reply_text(
            "📰 قسم الأخبار جاهز كواجهة، لكن جمع الأخبار يحتاج مصدر أخبار/بحث خارجي.\n"
            "النسخة الحالية لا تعرض إشاعات على أنها أخبار مؤكدة."
        )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("MediaVerse Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
