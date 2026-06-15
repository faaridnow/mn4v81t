import requests
import re

# Bütün fərqli kanalları və onların saytlarını buraya əlavə edəcəksiniz
kanallar = [
    {"ad": "AzTV", "url": "https://numnune-sayt.com/aztv-canli-yayim"},
    {"ad": "Kanal D", "url": "https://basqa-bir-sayt.net/kanal-d-izle"},
    {"ad": "İdman TV", "url": "https://idman-sayti.org/stream"}
]

with open("channels.m3u", "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n") # M3U standart başlığı
    
    for kanal in kanallar:
        try:
            # Brauzer kimi davranmaq üçün User-Agent əlavə edirik
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
            
            # Saytın kodunu yükləyirik
            response = requests.get(kanal["url"], headers=headers, timeout=15)
            
            # Səhifənin içindəki .m3u8 uzantılı linki axtarırıq
            m3u8_tapildi = re.search(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', response.text)
            
            if m3u8_tapildi:
                canli_link = m3u8_tapildi.group(0)
                f.write(f'#EXTINF:-1, {kanal["ad"]}\n')
                f.write(f'{canli_link}\n')
                print(f'[UĞURLU] {kanal["ad"]} yeniləndi.')
            else:
                print(f'[XƏTA] {kanal["ad"]} üçün saytın içində m3u8 linki tapılmadı.')
                
        except Exception as e:
            print(f'[XƏTA] {kanal["ad"]} saytına qoşulmaq alınmadı: {e}')
