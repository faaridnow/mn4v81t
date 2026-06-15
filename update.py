import requests
import re

# Bütün kanalların siyahısı
kanallar = [
    {
        "ad": "AzTV", 
        "url": "https://aztv.az/az/live",
        "qorunma": True,
        "referer": "https://yodaplayer.yodacdn.net/"
    }
]

with open("channels.m3u", "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    
    for kanal in kanallar:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            
            # 1. Əsas səhifəyə daxil oluruq
            response = requests.get(kanal["url"], headers=headers, timeout=15)
            
            # Səhifənin içindən yodaplayer linkini və ya birbaşa m3u8-i tapmağa çalışırıq
            # Əgər qorunan kanaldırsa, onun Referer parametrlərini linkə yapışdırırıq
            m3u8_tapildi = re.search(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', response.text)
            
            if m3u8_tapildi:
                canli_link = m3u8_tapildi.group(0)
                
                # Əgər kanalın Referer qoruması varsa, linkin sonuna əlavə edirik
                if kanal.get("qorunma"):
                    canli_link = f"{canli_link}|Origin={kanal['referer']}&Referer={kanal['referer']}&User-Agent=Mozilla/5.0"
                
                f.write(f'#EXTINF:-1, {kanal["ad"]}\n')
                f.write(f'{canli_link}\n')
                print(f'[UĞURLU] {kanal["ad"]} yeniləndi.')
            else:
                # Əgər birbaşa HTML daxilində tapılmadısa, deməli iframe daxilindədir.
                # Bura gələcəkdə həmin o gizli API linkini əlavə edəcəyik.
                print(f'[Yoxlama] {kanal["ad"]} üçün alternativ metod tələb olunur.')
                
        except Exception as e:
            print(f'[XƏTA] {kanal["ad"]} xətası: {e}')
