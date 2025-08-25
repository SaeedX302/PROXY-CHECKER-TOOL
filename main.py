import os
import time
import socket
import asyncio
import aiohttp
import geoip2.database
from aiogram.filters import Command
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
GEOIP_DB_PATH = os.getenv("GEOIP_DB_PATH", "GeoLite2-Country.mmdb")
AUTO_CLEANUP = os.getenv("AUTO_CLEANUP", "True") == "True"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing in environment variables.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Error handling for GeoIP database
try:
    geoip_reader = geoip2.database.Reader(GEOIP_DB_PATH)
except FileNotFoundError:
    print(f"Error: GeoIP database not found at {GEOIP_DB_PATH}. Country detection will be disabled.")
    geoip_reader = None

working_proxies = {'http': [], 'https': [], 'socks4': [], 'socks5': []}
country_proxies = {}
stop_flag = False
executor_pool = ThreadPoolExecutor(max_workers=100) # Increased workers for faster checking
start_time = datetime.now()
auto_cleanup = AUTO_CLEANUP
error_log = "error.log"
user_data = {"users": set(), "proxies_checked": 0}


async def log_error(msg):
    async with asyncio.Lock():
        with open(error_log, 'a') as f:
            f.write(f"{datetime.now()} - {msg}\n")


async def ping_proxy(ip, port):
    try:
        start_t = time.time()
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=2)
        writer.close()
        await writer.wait_closed()
        return round((time.time() - start_t) * 1000, 2)
    except:
        return None

async def check_proxy(proxy_line, proxy_type, session):
    if stop_flag:
        return None
    proxy_line = proxy_line.strip()
    if not proxy_line or ':' not in proxy_line:
        return None

    ip, port = proxy_line.split(':', 1)
    proxy_url = f"{proxy_type}://{proxy_line}"

    try:
        async with session.get('https://api.ipify.org', proxy=proxy_url, timeout=5) as resp:
            if resp.status == 200:
                ping = await ping_proxy(ip, port)
                country = 'unknown'
                if geoip_reader:
                    try:
                        country = geoip_reader.country(ip).country.name.lower()
                    except geoip2.errors.AddressNotFoundError:
                        pass # IP not in database
                return {'proxy': proxy_line, 'type': proxy_type, 'country': country, 'ping': ping}
    except Exception as e:
        # await log_error(f"Error checking {proxy_line} ({proxy_type}): {e}")
        return None
    return None


async def process_proxies(proxies, message: types.Message):
    global working_proxies, country_proxies, stop_flag
    working_proxies = {'http': [], 'https': [], 'socks4': [], 'socks5': []} # Reset for each new check
    country_proxies = {}
    stop_flag = False
    proxy_types = ['http', 'socks4', 'socks5'] # Removed https as http check covers it

    await message.answer("✅ Proxies check karna shuru kar diya hai...")
    progress_msg = await message.answer("🔍 Progress: [                    ] 0%")

    tasks = []
    async with aiohttp.ClientSession() as session:
        for proxy in proxies:
            for proxy_type in proxy_types:
                tasks.append(check_proxy(proxy, proxy_type, session))

        processed = 0
        total = len(tasks)
        for future in asyncio.as_completed(tasks):
            result = await future
            processed += 1
            if processed % 20 == 0 or processed == total: # Update progress less frequently to avoid API limits
                percent = int((processed / total) * 100)
                bar = "█" * (percent // 5) + " " * (20 - percent // 5)
                try:
                    await progress_msg.edit_text(f"🔍 Progress: [{bar}] {percent}% ({processed}/{total})")
                except:
                    pass

            if result:
                t = result['type']
                working_proxies[t].append(result['proxy'])
                country_key = f"{t}_{result['country']}"
                country_proxies.setdefault(country_key, []).append(result['proxy'])

    total_working = sum(len(v) for v in working_proxies.values())
    await message.answer(f"✅ Kaam ho gaya! {total_working} working proxies mil gayin.")
    if total_working > 0:
        await save_results(message)


async def save_results(message: types.Message):
    for proxy_type, proxies in working_proxies.items():
        if proxies:
            filename = f"{proxy_type}_working_{int(time.time())}.txt"
            with open(filename, 'w') as f:
                f.write('\n'.join(proxies))
            
            # Send the file as a document
            file_input = InputFile(filename)
            await message.answer_document(file_input, caption=f"✨ Here are your working {proxy_type.upper()} proxies!")

            if auto_cleanup:
                os.remove(filename)

# Naya function free proxies fetch karne ke liye
async def fetch_free_proxies():
    urls = [
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks4&timeout=10000&country=all",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all"
    ]
    proxies = []
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        text = await response.text()
                        proxies.extend(text.strip().split('\n'))
            except Exception as e:
                await log_error(f"Could not fetch proxies from {url}: {e}")
    return proxies


def main_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Help", callback_data="help")],
        [InlineKeyboardButton(text="🌐 Free Proxies", callback_data="free_proxies")], # Naya button
        [
            InlineKeyboardButton(text="🛑 Stop", callback_data="stop"),
            InlineKeyboardButton(text="📊 Uptime", callback_data="uptime")
        ],
    ])
    return kb


@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_data["users"].add(message.from_user.id)
    await message.answer(
        "👋 **Proxy Checker Bot mein Khush Amdeed**\n"
        "Check karne ke liye proxies wali `.txt` file send karein, ya 'Free Proxies' button dabayein.",
        reply_markup=main_keyboard(), parse_mode="Markdown"
    )

# ... (baaki help, stop, uptime handlers same rahenge)

@dp.callback_query(F.data == 'help')
async def show_help(callback_query: types.CallbackQuery):
    text = (
        "📌 **Available Commands:**\n\n"
        "✅ `/start` - Bot ko start karein\n"
        "✅ `/help` - Madad ke liye yeh message dekhein\n"
        "✅ `/stop` - Checking ka process rokein\n"
        "✅ `/up` - Bot ka uptime dekhein\n\n"
        "ℹ️ `IP:PORT` format mein proxies wali `.txt` file upload karein ya free proxies ke liye button use karein."
    )
    await callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=main_keyboard())


@dp.callback_query(F.data == 'stop')
async def stop_process(callback_query: types.CallbackQuery):
    global stop_flag
    stop_flag = True
    await callback_query.answer("⛔ Process roka ja raha hai...", show_alert=True)


@dp.callback_query(F.data == 'uptime')
async def show_uptime(callback_query: types.CallbackQuery):
    now = datetime.now()
    uptime = now - start_time
    days, r = divmod(uptime.total_seconds(), 86400)
    hours, r = divmod(r, 3600)
    minutes, seconds = divmod(r, 60)
    await callback_query.answer(
        f"✅ Bot Uptime: {int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s",
        show_alert=True
    )

# Free Proxies ke liye naya handler
@dp.callback_query(F.data == 'free_proxies')
async def get_free_proxies(callback_query: types.CallbackQuery):
    await callback_query.answer("🌐 Free proxies fetch ki ja rahi hain...", show_alert=False)
    proxies = await fetch_free_proxies()
    if proxies:
        await callback_query.message.answer(f"Fetched {len(proxies)} proxies. Ab inko check kiya ja raha hai...")
        await process_proxies(proxies, callback_query.message)
    else:
        await callback_query.message.answer("❌ Maaf kijiye, abhi free proxies fetch nahi ho sakin.")

@dp.message(F.document)
async def handle_file(message: types.Message):
    if not message.document.file_name.endswith('.txt'):
        await message.answer("Janab, sirf `.txt` file hi qubool ki jayegi.")
        return

    file = await message.document.get_file()
    file_path = f"downloads/{message.document.file_name}"
    os.makedirs("downloads", exist_ok=True)
    await bot.download_file(file.file_path, file_path)

    with open(file_path, 'r') as f:
        proxies = [line.strip() for line in f if line.strip()]

    if auto_cleanup:
        os.remove(file_path)

    user_data["proxies_checked"] += len(proxies)
    await process_proxies(proxies, message)


async def on_startup(bot: Bot):
    # Yeh hai deployment fix
    await bot.delete_webhook(drop_pending_updates=True)
    print("Webhook delete kar diya gaya hai. Polling shuru ho rahi hai...")


async def main():
    dp.startup.register(on_startup) # startup function register karein
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
