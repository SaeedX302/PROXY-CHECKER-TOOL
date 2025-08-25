import os
import time
import socket
import asyncio
import aiohttp
import geoip2.database
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from dotenv import load_dotenv
import geoip2.database

# Load .env locally
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS").split(",")]
GEOIP_DB_PATH = os.getenv("GEOIP_DB_PATH")
AUTO_CLEANUP = os.getenv("AUTO_CLEANUP") == "True"

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

geoip_reader = geoip2.database.Reader(GEOIP_DB_PATH)

working_proxies = {'http': [], 'https': [], 'socks4': [], 'socks5': []}
country_proxies = {}
stop_flag = False
executor_pool = ThreadPoolExecutor(max_workers=50)
start_time = datetime.now()
auto_cleanup = True
error_log = "error.log"
user_data = {"users": set(), "proxies_checked": 0}

async def log_error(msg):
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
    loop = asyncio.get_event_loop()
    tasks = []
    total = len(proxies) * len(proxy_types)
    processed = 0

    progress_msg = await message.answer("🔍 Progress: [                    ] 0%")

    for proxy in proxies:
        for proxy_type in proxy_types:
            tasks.append(loop.run_in_executor(executor_pool, lambda: asyncio.run(check_proxy(proxy, proxy_type))))

    for future in asyncio.as_completed(tasks):
        if stop_flag:
            break
        result = await future
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

# Inline Keyboard Buttons
def main_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Help", callback_data="help"),
        InlineKeyboardButton("🛑 Stop", callback_data="stop"),
        InlineKeyboardButton("📊 Uptime", callback_data="uptime")
    )
    return kb

@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    user_data["users"].add(message.from_user.id)
    await message.answer("👋 **Welcome to Proxy Checker Bot**\nSend a `.txt` file with proxies to check.", reply_markup=main_keyboard(), parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data == 'help')
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

@dp.callback_query_handler(lambda c: c.data == 'stop')
async def stop_process(callback_query: types.CallbackQuery):
    global stop_flag
    stop_flag = True
    await callback_query.message.answer("⛔ Stopping process...")

@dp.callback_query_handler(lambda c: c.data == 'uptime')
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

# ADMIN COMMANDS
@dp.message_handler(commands=['broadcast'])
async def broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply("❌ You are not authorized.")
    text = message.get_args()
    for user_id in user_data["users"]:
        try:
            await bot.send_message(user_id, f"📢 Broadcast: {text}")
        except:
            pass
    await message.reply("✅ Broadcast sent.")

@dp.message_handler(commands=['stats'])
async def stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply("❌ You are not authorized.")
    total_users = len(user_data["users"])
    total_checked = user_data["proxies_checked"]
    success = sum(len(v) for v in working_proxies.values())
    await message.reply(f"📊 **Stats**\nUsers: {total_users}\nChecked Today: {total_checked}\nSuccess: {success}", parse_mode="Markdown")

@dp.message_handler(commands=['logs'])
async def logs(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply("❌ You are not authorized.")
    if os.path.exists(error_log):
        await message.answer_document(InputFile(error_log))
    else:
        await message.reply("✅ No errors logged yet.")

@dp.message_handler(commands=['cleanup'])
async def cleanup_toggle(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.reply("❌ You are not authorized.")
    global auto_cleanup
    arg = message.get_args().lower()
    if arg == 'on':
        auto_cleanup = True
        await message.reply("✅ Auto-cleanup enabled.")
    elif arg == 'off':
        auto_cleanup = False
        await message.reply("✅ Auto-cleanup disabled.")
    else:
        await message.reply("Usage: `/cleanup on|off`", parse_mode="Markdown")

@dp.message_handler(content_types=['document'])
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

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)