import os
import time
import asyncio
import aiohttp
import geoip2.database
from aiogram.filters import Command
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from datetime import datetime
from dotenv import load_dotenv
from aiogram.exceptions import TelegramRetryAfter

# --- Environment Variables ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
GEOIP_DB_PATH = os.getenv("GEOIP_DB_PATH", "GeoLite2-Country.mmdb")
AUTO_CLEANUP = os.getenv("AUTO_CLEANUP", "True") == "True"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN must be set in environment variables.")

# --- Bot Setup ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Global Variables & Error Handling ---
try:
    geoip_reader = geoip2.database.Reader(GEOIP_DB_PATH)
except FileNotFoundError:
    print(f"⚠️ GeoIP database not found at {GEOIP_DB_PATH}. Country detection will be disabled.")
    geoip_reader = None

start_time = datetime.now()
error_log = "error.log"

# --- Core Functions ---
async def log_error(msg):
    # This function logs errors to a file for debugging.
    async with asyncio.Lock():
        with open(error_log, 'a') as f:
            f.write(f"[{datetime.now()}] - {msg}\n")

async def check_proxy(proxy_line: str, session: aiohttp.ClientSession):
    # This is the main function to check a single proxy.
    proxy_line = proxy_line.strip()
    if ':' not in proxy_line:
        return None

    ip, port, *rest = proxy_line.split(':')
    proxy_url = f"http://{ip}:{port}" # Check as HTTP, it often works for SOCKS too for this test

    try:
        # We test the proxy by trying to fetch our IP from ipify.org through it
        async with session.get('https://api.ipify.org', proxy=proxy_url, timeout=10) as resp:
            if resp.status == 200:
                return {'proxy': proxy_line}
    except Exception:
        # Most proxies will fail, so we don't log every single failure.
        pass
    return None

async def process_proxies(proxies: list, message: types.Message):
    # This function manages the whole process of checking a list of proxies.
    working_proxies = []
    
    await message.answer(f"✅ Aapki {len(proxies)} proxies ko check karna shuru kar diya hai...")
    progress_msg = await message.answer("🔍 Progress: [                    ] 0%")
    last_update_time = time.time()

    tasks = []
    # Create one session for all requests for better performance.
    async with aiohttp.ClientSession() as session:
        for proxy in proxies:
            tasks.append(check_proxy(proxy, session))

        processed = 0
        total = len(tasks)
        for future in asyncio.as_completed(tasks):
            result = await future
            processed += 1
            
            # Flood Control Fix: Update progress bar only every 2 seconds to avoid Telegram limits
            current_time = time.time()
            if current_time - last_update_time > 2 or processed == total:
                percent = int((processed / total) * 100)
                bar = "█" * (percent // 5) + " " * (20 - percent // 5)
                try:
                    await progress_msg.edit_text(f"🔍 Progress: [{bar}] {percent}% ({processed}/{total})")
                    last_update_time = current_time
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after) # Wait if we still get a flood error
                except Exception: # Ignore other errors like "message not modified"
                    pass

            if result:
                working_proxies.append(result['proxy'])

    if not working_proxies:
        await message.answer("🙁 Afsos, koi bhi working proxy nahi mili.")
        return

    await message.answer(f"🎉 Mubarak! {len(working_proxies)} working proxies mil gayin.")
    
    # Save results to a file and send to the user.
    filename = f"valid_proxy_{int(time.time())}.txt"
    with open(filename, 'w') as f:
        f.write('\n'.join(working_proxies))
    
    await message.answer_document(InputFile(filename), caption="✨ Yeh rahi aapki fresh & valid proxies!")
    if AUTO_CLEANUP:
        os.remove(filename)

async def fetch_free_proxies():
    # Fetches a list of free proxies from an API.
    url = "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    return text.strip().split('\n')
    except Exception as e:
        await log_error(f"Could not fetch free proxies: {e}")
    return []

# --- UI and Keyboards ---
def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Free Proxies", callback_data="free_proxies")],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="help"), InlineKeyboardButton(text="📊 Uptime", callback_data="uptime")]
    ])

# --- Bot Handlers ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 **Proxy Checker Bot (Render Version)**\n"
        "Apni proxies check karney ke liye `.txt` file send karein.",
        reply_markup=main_keyboard(), parse_mode="Markdown"
    )

@dp.callback_query(F.data == 'help')
async def show_help(callback_query: types.CallbackQuery):
    text = "**Kaise Istemaal Karein?**\n\n1️⃣ **Custom Proxies**: Apni `IP:PORT` format mein proxies wali `.txt` file is chat mein send karein.\n\n2️⃣ **Free Proxies**: `🌐 Free Proxies` ka button dabayein."
    await callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=main_keyboard())
    await callback_query.answer()

@dp.callback_query(F.data == 'uptime')
async def show_uptime(callback_query: types.CallbackQuery):
    uptime = datetime.now() - start_time
    days, r = divmod(uptime.total_seconds(), 86400)
    hours, r = divmod(r, 3600)
    minutes, _ = divmod(r, 60)
    await callback_query.answer(f"🚀 Bot Uptime: {int(days)}d {int(hours)}h {int(minutes)}m", show_alert=True)

@dp.callback_query(F.data == 'free_proxies')
async def get_free_proxies(callback_query: types.CallbackQuery):
    await callback_query.answer("🌐 Free proxies dhoond raha hoon...", show_alert=False)
    proxies = await fetch_free_proxies()
    if proxies:
        await process_proxies(proxies, callback_query.message)
    else:
        await callback_query.message.answer("❌ Maaf kijiye, abhi free proxies nahi mil sakin. Baad mein try karein.")

@dp.message(F.document)
async def handle_file(message: types.Message):
    if not message.document.file_name.endswith('.txt'):
        await message.answer("❗️ File `.txt` format mein honi chahiye.")
        return

    # AttributeError Fix: This is the correct way to get the file object in aiogram v3
    file_info = await bot.get_file(message.document.file_id)
    
    file_path = f"downloads/{message.document.file_name}"
    os.makedirs("downloads", exist_ok=True)
    await bot.download_file(file_info.file_path, destination=file_path)

    with open(file_path, 'r') as f:
        proxies = [line.strip() for line in f if line.strip()]

    if AUTO_CLEANUP:
        if os.path.exists(file_path): os.remove(file_path)
    
    if proxies:
        await process_proxies(proxies, message)
    else:
        await message.answer("⚠️ Aapki file khali hai ya usmein proxies nahi hain.")

# --- Main Execution for Polling ---
async def on_startup(bot: Bot):
    # This function runs when the bot starts up.
    # It deletes any lingering webhooks to ensure polling works correctly.
    await bot.delete_webhook(drop_pending_updates=True)
    print("--- Bot Started Successfully on Render (Polling Mode) ---")

async def main():
    # Register the startup function
    dp.startup.register(on_startup)
    # Start the bot using long polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
