import cloudscraper
import time

s = cloudscraper.create_scraper()
url = "https://dl.snapcdn.app/get?token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1cmwiOiJodHRwczovL3YxNi50b2tjZG4uY29tLzhlMGE4ODQ0YzZiMDMxMWYzMjI2ZWY3MWQ0OTdkMzE3LzY5OTI1ZTAwLzc2MDc0ODMxNTUyNTEwODg2NjBfb3JpZ2luYWwubXA0P2RsPTEiLCJmaWxlbmFtZSI6IlNuYXBUaWsuTmV0Xzc2MDc0ODMxNTUyNTEwODg2NjBfaGQubXA0IiwibmJmIjoxNzc4Nzg0Njg4LCJleHAiOjE3Nzg3ODgyODgsImlhdCI6MTc3ODc4NDY4OH0.vC6WFmrsJBZXfnVKgXSfIDgHaet8oxPQ01w4b67t0DI"
print("Connecting...")
try:
    with s.get(url, stream=True, timeout=10) as r:
        print("Connected:", r.status_code)
        size = 0
        t0 = time.time()
        for chunk in r.iter_content(chunk_size=1024*1024):
            if not chunk: continue
            size += len(chunk)
            print(f"Downloaded: {size/1024/1024:.1f} MB in {time.time()-t0:.1f}s")
except Exception as e:
    print("Error:", e)
