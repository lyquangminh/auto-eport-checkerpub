import os
import json
import asyncio
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ------------------------------------------------------------------------------
# 1. HÀM TRA CÚU EPORT BẮT RESPONSE (HỖ TRỢ CÁT LÁI VÀ GIANG NAM)
# ------------------------------------------------------------------------------
async def check_eport_single_site(container_string, site="CTL"):
    url = "https://eport.saigonnewport.com.vn/ContainerInformation"
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1366, 'height': 768}
        )
        page = await context.new_page()
        
        # Từ khóa khớp đúng text trong dropdown ePort
        target_text = "CTL - Cát Lái" if site.upper() == "CTL" else "GNL - Cát Lái Giang Nam"
        
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)

            # Chọn Khu vực (Cát Lái / Giang Nam)
            try:
                site_box = page.locator('#SITE_ID, .dx-lookup, .dx-selectbox').first
                if await site_box.count() > 0:
                    await site_box.click()
                    await asyncio.sleep(0.8)
                    option = page.locator(f'.dx-item-content:has-text("{target_text}")').first
                    if await option.count() > 0:
                        await option.click()
                        await asyncio.sleep(0.5)
            except Exception as e_site:
                print(f"   ⚠️ Lỗi chọn khu vực {target_text}: {e_site}")

            # Điền danh sách Container (ngăn cách bởi dấu phẩy)
            inner_textarea = page.locator('#txtContainerNos textarea')
            if await inner_textarea.count() > 0:
                await inner_textarea.fill(container_string)
            else:
                await page.click('#txtContainerNos')
                await page.keyboard.type(container_string)

            # Click Tìm kiếm & Chờ Response từ Server
            search_btn = page.locator('.dx-button:has-text("Tìm kiếm"), button:has-text("Tìm kiếm")').first
            async with page.expect_response(lambda r: "ContainerInformation" in r.url and r.status == 200, timeout=20000) as response_info:
                await search_btn.click()

            response = await response_info.value
            response_text = await response.text()
            await asyncio.sleep(1.5)

            results = {}
            soup = BeautifulSoup(response_text, 'html.parser')
            rows = soup.find_all('tr')
            if not rows:
                soup = BeautifulSoup(await page.content(), 'html.parser')
                rows = soup.find_all('tr')

            conts_list = [c.strip() for c in container_string.split(',') if c.strip()]
            
            # Quét từng hàng dữ liệu
            for row in rows:
                cols = row.find_all('td')
                if not cols:
                    continue
                
                row_str = row.get_text()
                for cont in conts_list:
                    if cont in row_str:
                        # Vị trí các cột trong table ePort:
                        # cols[0]: Checkbox
                        # cols[1]: Số Container (Container No.)
                        # cols[2]: Thời gian (Time)
                        # cols[3]: Vị trí container (Location)
                        # cols[4]: Đã nhập bãi cảng (In yard) -> "Y" / "N"
                        
                        in_yard_val = ""
                        if len(cols) >= 5:
                            in_yard_val = cols[4].get_text().strip()
                        elif len(cols) >= 4:
                            in_yard_val = cols[3].get_text().strip()

                        status = "Đã hạ" if in_yard_val.upper() == 'Y' else "Chưa hạ"
                        
                        # Ưu tiên ghi nhận "Đã hạ" nếu tìm thấy
                        if cont not in results or results[cont] == "Chưa hạ":
                            results[cont] = status

            await browser.close()
            return results

        except Exception as e:
            print(f"   ❌ Lỗi tra cứu ePort site [{site}]: {e}")
            await browser.close()
            return {}

# ------------------------------------------------------------------------------
# 2. HÀM CHECK TỔNG HỢP: TRA CTL TRƯỚC, TỰ CHUYỂN GNL NẾU CHƯA HẠ
# ------------------------------------------------------------------------------
async def check_eport_all_sites(container_string):
    # Bước 1: Quét tại Cát Lái (CTL)
    results = await check_eport_single_site(container_string, site="CTL")
    
    conts_list = [c.strip() for c in container_string.split(',') if c.strip()]
    missing_or_unloaded = [c for c in conts_list if results.get(c) != "Đã hạ"]

    # Bước 2: Nếu còn cont "Chưa hạ" hoặc chưa thấy, quét tiếp Giang Nam (GNL)
    if missing_or_unloaded:
        gnl_string = ", ".join(missing_or_unloaded)
        gnl_results = await check_eport_single_site(gnl_string, site="GNL")
        for cont, status in gnl_results.items():
            if status == "Đã hạ":
                results[cont] = "Đã hạ"

    # Đảm bảo cont nào không tìm thấy dữ liệu cũng được gán "Chưa hạ"
    for cont in conts_list:
        if cont not in results:
            results[cont] = "Chưa hạ"

    return results

# ------------------------------------------------------------------------------
# 3. LUỒNG CHÍNH THỰC THI THU THẬP BIẾN MÔI TRƯỜNG & GỬI CALLBACK
# ------------------------------------------------------------------------------
async def main():
    row_index = os.getenv("ROW_INDEX")
    job_no = os.getenv("JOB_NO", "")
    conts_str = os.getenv("CONTS_STR", "")
    callback_url = os.getenv("CALLBACK_URL", "")
    passcode = os.getenv("PASSCODE", "")

    if not conts_str or not callback_url:
        print("❌ Thiếu dữ liệu đầu vào (CONTS_STR hoặc CALLBACK_URL). Dừng chương trình.")
        return

    print(f"🚀 Bắt đầu xử lý Dòng {row_index} - JOB: {job_no}")
    print(f"📦 Danh sách Cont: {conts_str}")

    # Thực thi tra cứu
    res_dict = await check_eport_all_sites(conts_str)

    # Format chuỗi kết quả ghi vào Cột H (Ví dụ: "TXGU5668794: Đã hạ")
    output_lines = [f"{cont}: {status}" for cont, status in res_dict.items()]
    final_result_text = "\n".join(output_lines)

    print(f"✅ Kết quả tra cứu:\n{final_result_text}")

    # Gửi POST Request trả kết quả về Google Apps Script
    payload = {
        "passcode": passcode,
        "row_index": int(row_index),
        "result_text": final_result_text
    }

    try:
        print(f"📡 Đang gửi kết quả về GAS Webhook để ghi vào Cột H dòng {row_index}...")
        resp = requests.post(callback_url, json=payload, timeout=30)
        print(f"🎉 Phản hồi từ GAS: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"❌ Lỗi gửi Webhook về GAS: {e}")

if __name__ == "__main__":
    asyncio.run(main())
