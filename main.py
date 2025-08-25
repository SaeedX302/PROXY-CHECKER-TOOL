import os
import time
import socket
import asyncio
import aiohttp
import geoip2.databas
from aiogram.filters import Command
from aiogram import Bot, Dispatcher, types
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

# Validate required env
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing in environment variables.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

geoip_reader = geoip2.database.Reader(GEOIP_DB_PATH)

working_proxies = {'http': [], 'https': [], 'socks4': [], 'socks5': []}
country_proxies = {}
stop_flag = False
executor_pool = ThreadPoolExecutor(max_workers=50)
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
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect((ip, int(port)))
        sock.close()
        return round((time.time() - start) * 1000, 2)
    except:
        return None


async def check_proxy(proxy_line, proxy_type):
    if stop_flag:
        return None
    proxy_line = proxy_line.strip()
    if not proxy_line:
        return None

    parts = proxy_line.split(':')
    if len(parts) < 2:
        return None

    ip, port = parts[0], parts[1]
    proxy = f"{ip}:{port}"
    proxy_dict = {
        'http': f"{proxy_type}://{proxy}",
        'https': f"{proxy_type}://{proxy}"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.ipify.org', proxy=proxy_dict['http'], timeout=3) as resp:
                if resp.status == 200:
                    ping = await ping_proxy(ip, port)
                    country = 'unknown'
                    try:
                        country = geoip_reader.country(ip).country.name.lower()
                    except:
                        pass
                    return {'proxy': proxy_line, 'type': proxy_type, 'country': country, 'ping': ping}
    except Exception as e:
        await log_error(str(e))
        return None


async def process_proxies(proxies, message: types.Message):
    global working_proxies, country_proxies, stop_flag
    stop_flag = False
    proxy_types = ['http', 'https', 'socks4', 'socks5']

    await message.answer("✅ Starting proxy check...")
    progress_msg = await message.answer("🔍 Progress: [                    ] 0%")

    async def check_and_update(proxy, proxy_type):
        return await check_proxy(proxy, proxy_type)

    total = len(proxies) * len(proxy_types)
    processed = 0

    for proxy in proxies:
        for proxy_type in proxy_types:
            result = await check_and_update(proxy, proxy_type)
            processed += 1
            percent = int((processed / total) * 100)
            bar = "█" * (percent // 5) + " " * (20 - percent // 5)
            await progress_msg.edit_text(f"🔍 Progress: [{bar}] {percent}%")

            if result:
                t = result['type']
                working_proxies[t].append(result['proxy'])
                country_key = f"{t}_{result['country']}"
                if country_key not in country_proxies:
                    country_proxies[country_key] = []
                country_proxies[country_key].append(result['proxy'])

    await message.answer(f"✅ Done! {sum(len(v) for v in working_proxies.values())} working proxies found.")
    await save_results(message)


async def save_results(message: types.Message):
    for proxy_type, proxies in working_proxies.items():
        if proxies:
            filename = f"{proxy_type}_working.txt"
            with open(filename, 'w') as f:
                f.write('\n'.join(proxies))
            await message.answer_document(InputFile(filename))
            if auto_cleanup:
                os.remove(filename)


def main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Help", callback_data="help"),
        InlineKeyboardButton("🛑 Stop", callback_data="stop"),
        InlineKeyboardButton("📊 Uptime", callback_data="uptime")
    )
    return kb


@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_data["users"].add(message.from_user.id)
    await message.answer("👋 **Welcome to Proxy Checker Bot**\nSend a `.txt` file with proxies to check.",
                         reply_markup=main_keyboard(), parse_mode="Markdown")


@dp.callback_query(lambda c: c.data == 'help')
async def show_help(callback_query: types.CallbackQuery):
    text = (
        "📌 **Available Commands:**\n\n"
        "✅ `/start` - Start the bot\n"
        "✅ `/help` - Show this help message\n"
        "✅ `/stop` - Stop checking process\n"
        "✅ `/up` - Show bot uptime\n"
        "\nℹ️ Upload a `.txt` file with proxies in `IP:PORT` format."
    )
    await callback_query.message.edit_text(text, parse_mode="Markdown", reply_markup=main_keyboard())


@dp.callback_query(lambda c: c.data == 'stop')
async def stop_process(callback_query: types.CallbackQuery):
    global stop_flag
    stop_flag = True
    await callback_query.message.answer("⛔ Stopping process...")


@dp.callback_query(lambda c: c.data == 'uptime')
async def show_uptime(callback_query: types.CallbackQuery):
    now = datetime.now()
    uptime = now - start_time
    days, seconds = uptime.days, uptime.seconds
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    await callback_query.message.answer(
        f"✅ **Bot Uptime:** {days}d {hours}h {minutes}m {seconds}s",
        parse_mode="Markdown"
    )


@dp.message(content_types=['document'])
async def handle_file(message: types.Message):
    file = await message.document.get_file()
    file_path = f"downloads/{message.document.file_name}"
    os.makedirs("downloads", exist_ok=True)
    await bot.download_file(file.file_path, file_path)

    with open(file_path, 'r') as f:
        proxies = f.readlines()

    if auto_cleanup:
        os.remove(file_path)

    user_data["proxies_checked"] += len(proxies)
    await process_proxies(proxies, message)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

