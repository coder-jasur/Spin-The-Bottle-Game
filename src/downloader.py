import os
import requests

BASE_URL = "https://butilochka.cdnvideo.ru/bottle/bundle/"
SAVE_DIR = os.path.join("src", "app", "site", "bottle", "bundle")

def download_asset(fragment: str):
    """
    Kiritilgan URL fragmentini to'liq URL'ga aylantirib, yuklab oladi
    va mos papkaga saqlaydi.
    """
    # Agar foydalanuvchi to'liq "http://localhost:8000/" manzilini tashlagan bo'lsa, uni kesib tashlaymiz
    if "localhost:8000/" in fragment:
        fragment = fragment.split("localhost:8000/")[1]
    elif "://" in fragment:
        # Boshqa har qanday domen bo'lsa ham faqat yo'lini olib qolamiz (masalan http://.../300/...)
        from urllib.parse import urlparse
        fragment = urlparse(fragment).path.lstrip('/')
        if '?' in fragment:
            fragment += '?' + fragment.split('?')[1]
            
    # 1. Fragmentdan ortiqcha so'rov qismini (masalan, ?9) olib tashlaymiz
    clean_fragment = fragment.split('?')[0].strip('/')
    
    # 2. To'liq URL manzilini yig'amiz
    full_url = BASE_URL + fragment.lstrip('/')
    
    # 3. Saqlanadigan to'liq manzilni aniqlaymiz
    local_path = os.path.join(SAVE_DIR, os.path.normpath(clean_fragment))
    
    # 4. Kerakli papkalar mavjud bo'lmasa, ularni yaratamiz
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    
    print(f"\n⏳ Yuklanmoqda: {full_url}")
    try:
        # Faylni yuklab olish
        response = requests.get(full_url, stream=True)
        response.raise_for_status() # Agar 404 yoki boshqa xato bo'lsa to'xtaydi
        
        # Faylni saqlash
        with open(local_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        print(f"✅ Muvaffaqiyatli saqlandi: {local_path}\n")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Yuklashda xatolik yuz berdi: {e}\n")


def main():
    print("==================================================")
    print("🚀 Fayllarni avtomatik yuklab oluvchi dastur")
    print(f"📁 Saqlash manzili: {SAVE_DIR}")
    print("❗️ Dasturdan chiqish uchun 'q' tugmasini bosing.")
    print("==================================================\n")
    
    while True:
        # Foydalanuvchidan input so'raymiz
        fragment = input("🔗 URL fragmentini kiriting (masalan: 300/s_rubyshoes_v2.json?9): ").strip()
        
        if fragment.lower() == 'q':
            print("Dastur tugatildi. Xayr!")
            break
            
        if fragment:
            download_asset(fragment)

if __name__ == "__main__":
    main()
