import os
import json
import asyncio
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ------------------------------------------------------------------------------
# 1. LẤY DỮ LIỆU TỪ DOCKER / GITHUB ACTIONS ENV (DO WEBHOOK TRUYỀN SANG)
# ------------------------------------------------------------------------------
JOB_NO = os.getenv("JOB_NO", "")
CONTS_STR = os.getenv("CONTS_STR", "")
CALLBACK_URL = os.getenv("CALLBACK_URL", "")
PASSCODE = os.getenv("PASSCODE", "")

# ------------------------------------------------------------------------------
# 2. HÀM BÓC TÁCH BẢNG EPORT THÔNG MINH
# ------------------------------------------------------------------------------
def parse_eport_html(html_content, container_string):
    conts_list = [c.strip() for c in container_string.split(',') if c.strip()]
    results = {c: "Chưa hạ" for c in conts_list}

    if not html_content:
        return results

    soup = BeautifulSoup(html_content, 'html.parser')
    rows = soup.find_all('tr')

    for row in rows:
        text = row.get_text(separator=' ', strip=True)
        for cont in conts_list:
            if cont in text:
                cells = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
                if cells:
                    results[cont] = " | ".join(cells)
                else:
                    results[cont] = text

    return results

# ------------------------------------------------------------------------------
# 3. TRUY CẬP EPORT & GỬI KẾT QUẢ VỀ LẠI GAS VIA WEBHOOK
# ------------------------------------------------------------------------------
async def process_eport_job(page, job_no, container_string):
    url = f"https://eport.saigonnewport.com.vn/Container/GetContainerList?jobNo={job_no}"
    try:
        await page.goto(url, timeout=30000)
        await page.wait_for_load_state("networkidle")
        html = await page.content()
        return parse_eport_html(html, container_string)
    except Exception as e:
        print(f"❌ Lỗi truy cập ePort cho Job {job_no}: {e}")
        return {c: f"Lỗi ePort ({e})" for c in container_string.split(',')}

async def main_async():
    if not JOB_NO or not CONTS_STR:
        print("❌ Thiếu JOB_NO hoặc CONTS_STR truyền từ Webhook Payload!")
        return

    print(f"🚀 Bắt đầu xử lý JOB: {JOB_NO}")
    print(f"📦 Danh sách Cont cần kiểm tra: {CONTS_STR}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        eport_res = await process_eport_job(page, JOB_NO, CONTS_STR)
        
        formatted_output = []
        for cont, status in eport_res.items():
            formatted_output.append(f"{cont}: {status}")
        
        final_text = "\n".join(formatted_output)
        print(f"✅ Kết quả tra cứu:\n{final_text}")

        await browser.close()

    # Gửi kết quả ngược lại cho GAS Web App
    if CALLBACK_URL:
        print(f"📡 Đang gửi kết quả về GAS Webhook ({CALLBACK_URL})...")
        payload = {
            "passcode": PASSCODE,
            "job_no": JOB_NO,
            "result_text": final_text
        }
        try:
            res = requests.post(CALLBACK_URL, json=payload, timeout=15)
            print(f"🎉 Phản hồi từ GAS: {res.status_code} - {res.text}")
        except Exception as e:
            print(f"❌ Lỗi khi gửi webhook về GAS: {e}")
    else:
        print("⚠️ Không tìm thấy CALLBACK_URL để trả kết quả.")

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    main()