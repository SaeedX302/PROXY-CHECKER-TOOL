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
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# --- Environment Variables ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
# Make sure to set WEBHOOK_URL in Vercel environment variables
# Example: https://your-project-name.vercel.app
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

GEOIP_DB_PATH = os.getenv("GEOIP_DB_PATH", "GeoLite2-Country.mmdb")
AUTO_CLEANUP = os.getenv("AUTO_CLEANUP", "True") == "True"

if not BOT_TOKEN or not WEBHOOK_URL:
    raise ValueError("BOT_TOKEN and WEBHOOK_URL must be set in environment variables.")

# --- Bot Setup ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Global Variables ---
try:
    geoip_reader = geoip2.database.Reader(GEOIP_DB_PATH)
except FileNotFoundError:
    print(f"⚠️ GeoIP database not found at {GEOIP_DB_PATH}. Country detection will be disabled.")
    geoip_reader = None
start_time = datetime.now()

# --- Core Functions (check_proxy, process_proxies, etc.) ---
# ... (Yeh functions pichle code se same rahenge, unhein yahan paste karein) ...
# NOTE: To save space, I am omitting the functions that have not changed.
# Please copy the functions `log_error`, `check_proxy`, `process_proxies`, `fetch_free_proxies`
# from our previous conversation and paste them here.
async def log_error(msg):
    # This function is for logging errors if needed.
    pass

async def check_proxy(proxy_line: str, session: aiohttp.ClientSession):
    proxy_line = proxy_line.strip()
    if ':' not in proxy_line: return None
    ip, port, *rest = proxy_line.split(':')
    proxy_url = f"http://{ip}:{port}"
    try:
        async with session.get('https://api.ipify.org', proxy=proxy_url, timeout=10) as resp:
            if resp.status == 200: return {'proxy': proxy_line}
    except Exception: pass
    return None

async def process_proxies(proxies: list, message: types.Message):
    working_proxies = []
    await bot.send_message(message.chat.id, f"✅ Aapki {len(proxies)} proxies ko check karna shuru kar diya hai...")
    progress_msg = await bot.send_message(message.chat.id, "🔍 Progress: [                    ] 0%")
    last_update_time = time.time()
    tasks = []
    async with aiohttp.ClientSession() as session:
        for proxy in proxies: tasks.append(check_proxy(proxy, session))
        processed = 0
        total = len(tasks)
        for future in asyncio.as_completed(tasks):
            result = await future
            processed += 1
            current_time = time.time()
            if current_time - last_update_time > 2 or processed == total:
                percent = int((processed / total) * 100)
                bar = "█" * (percent // 5) + " " * (20 - percent // 5)
                try:
                    await bot.edit_message_text(f"🔍 Progress: [{bar}] {percent}% ({processed}/{total})", chat_id=progress_msg.chat.id, message_id=progress_msg.message_id)
                    last_update_time = current_time
                except Exception: pass
            if result: working_proxies.append(result['proxy'])
    if not working_proxies:
        await bot.send_message(message.chat.id, "🙁 Afsos, koi bhi working proxy nahi mili.")
        return
    await bot.send_message(message.chat.id, f"🎉 Mubarak! {len(working_proxies)} working proxies mil gayin.")
    filename = f"valid_proxy_{int(time.time())}.txt"
    with open(filename, 'w') as f: f.write('\n'.join(working_proxies))
    await bot.send_document(message.chat.id, InputFile(filename), caption="✨ Yeh rahi aapki fresh & valid proxies!")
    if AUTO_CLEANUP and os.path.exists(filename): os.remove(filename)

async def fetch_free_proxies():
    url = "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    text = await response.text()
                    return text.strip().split('\n')
    except Exception as e: await log_error(f"Could not fetch free proxies: {e}")
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
        "👋 **Proxy Checker Bot (Webhook Version)**\n"
        "Apni proxies check karney ke liye `.txt` file send karein.",
        reply_markup=main_keyboard(), parse_mode="Markdown"
    )
    
@dp.callback_query(F.data == 'free_proxies')
async def get_free_proxies(callback_query: types.CallbackQuery):
    await callback_query.answer("🌐 Free proxies dhoond raha hoon...", show_alert=False)
    # Since process_proxies is long, we run it in the background
    asyncio.create_task(process_proxies(await fetch_free_proxies(), callback_query.message))

@dp.message(F.document)
async def handle_file(message: types.Message):
    if not message.document.file_name.endswith('.txt'):
        await message.answer("❗️ File `.txt` format mein honi chahiye.")
        return
    file_info = await bot.get_file(message.document.file_id)
    file_path = f"downloads/{message.document.file_name}"
    os.makedirs("downloads", exist_ok=True)
    await bot.download_file(file_info.file_path, destination=file_path)
    with open(file_path, 'r') as f:
        proxies = [line.strip() for line in f if line.strip()]
    if AUTO_CLEANUP and os.path.exists(file_path): os.remove(file_path)
    if proxies:
        # Run the long checking process in the background
        asyncio.create_task(process_proxies(proxies, message))
    else:
        await message.answer("⚠️ Aapki file khali hai.")

# --- Webhook Startup/Shutdown ---
async def on_startup(bot: Bot):
    # Set the webhook
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    print("--- Bot Started with Webhook ---")

async def on_shutdown(bot: Bot):
    # Remove the webhook
    await bot.delete_webhook()
    print("--- Bot Shut Down ---")

# --- Vercel Entry Point ---
# This `app` variable is what Vercel looks for.
app = web.Application()
dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)

# Create the webhook handler
webhook_handler = SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
)
# Register the handler
webhook_handler.register(app, path="/webhook")
# Start the web application
setup_application(app, dp, bot=bot)
