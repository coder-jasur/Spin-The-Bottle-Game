import asyncio
import aiohttp
import os
import sys
import json
import time

BASE_URL = "https://butilochka.cdnvideo.ru/bottle/bundle/"
SAVE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "app", "site", "bottle", "bundle"))

async def fetch_and_save(session, fragment, semaphore, progress):
    url = f"{BASE_URL}{fragment}"
    if '?' in fragment:
        save_path_str = fragment.split('?')[0]
    else:
        save_path_str = fragment
        
    save_path = os.path.normpath(os.path.join(SAVE_DIR, save_path_str))
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    
    # If file exists and size > 0, skip
    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
        progress['skipped'] += 1
        return
        
    async with semaphore:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.read()
                    with open(save_path, 'wb') as f:
                        f.write(data)
                    progress['success'] += 1
                else:
                    progress['failed'] += 1
        except Exception as e:
            progress['failed'] += 1

async def download_all(fragments):
    semaphore = asyncio.Semaphore(50)  # 50 concurrent downloads
    progress = {'success': 0, 'failed': 0, 'skipped': 0}
    
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(limit=50)
    
    start_time = time.time()
    
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = [fetch_and_save(session, frag, semaphore, progress) for frag in fragments]
        await asyncio.gather(*tasks)
        
    end_time = time.time()
    print(f"Downloaded {progress['success']} files. Skipped: {progress['skipped']}. Failed: {progress['failed']}.")
    print(f"Total time: {end_time - start_time:.2f} seconds.")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--urls':
        with open(sys.argv[2], 'r', encoding='utf-8') as f:
            fragments = f.read().splitlines()
        print(f"Loaded {len(fragments)} fragments to download.")
        asyncio.run(download_all(fragments))
    else:
        print("Usage: python fast_downloader.py --urls <fragments_file.txt>")
