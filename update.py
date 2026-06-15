import requests
import re

# Kanalların konfiqurasiyası
kanallar = [
    {
        "ad": "AzTV", 
        "url": "https://aztv.az/az/live",
        "stream_base": "https://str.yodacdn.net/azertv/tracks-v1a1/mono.ts.m3u8",
        "qorunma": True,
        "referer": "https://yodaplayer.yodacdn.net/"
    }
]

with open("channels.m3u", "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    
    for kanal in kanallar:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0'}
            
            # 1. Ana sayfaya daxil oluruq
            print(f'[{kanal["ad"]}] Ana sayfa yüklənir: {kanal["url"]}')
            response = requests.get(kanal["url"], headers=headers, timeout=15)
            
            # 2. İframe (Player) linkini tapırıq
            iframe_link = re.search(r'src=["\'](https?://[^"\']+?(?:yodaplayer|embed|player|cdn)[^"\']*?)["\']', response.text)
            
            if iframe_link:
                gizli_url = iframe_link.group(1)
                print(f'[{kanal["ad"]}] Gizli player linki tapıldı: {gizli_url}')
                
                # 3. Player səhifəsinə daxil olub gizli tokeni ovlayırıq
                iframe_headers = headers.copy()
                iframe_headers['Referer'] = kanal["url"]
                iframe_response = requests.get(gizli_url, headers=iframe_headers, timeout=15)
                
                # Regex ilə data-token-in içindəki uzun şifrəni çəkirik
                token_tapildi = re.search(r'data-token=["\']([^"\']+)["\']', iframe_response.text)
                
                if token_tapildi:
                    token = token_tapildi.group(1)
                    print(f'[{kanal["ad"]}] Canlı təhlükəsizlik tokeni uğurla tutuldu!')
                    
                    # 4. Token və axın linkini birləşdirib tam IPTV linkini qururuq
                    canli_link = f"{kanal['stream_base']}?token={token}"
                    
                    # Əgər Referer qoruması varsa, player-in başlıqlarını bura yapışdırırıq
                    if kanal.get("qorunma"):
                        canli_link = f"{canli_link}|Origin={kanal['referer']}&Referer={kanal['referer']}&User-Agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                    
                    f.write(f'#EXTINF:-1, {kanal["ad"]}\n')
                    f.write(f'{canli_link}\n')
                    print(f'[UĞURLU] {kanal["ad"]} linki uğurla yaradıldı və fayla yazıldı!')
                else:
                    print(f'[XƏTA] Səhifə daxilində data-token tapılmadı.')
            else:
                print(f'[XƏTA] İframe linki tapılmadı.')
                
        except Exception as e:
            print(f'[XƏTA] {kanal["ad"]} icrasında xəta: {e}')
